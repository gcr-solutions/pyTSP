from threading import Lock
from flask import Flask, jsonify, render_template, request, session
from flask_socketio import emit, SocketIO
from forms import CreationForm
from json import dumps, load
from os.path import abspath, dirname, join
from sqlalchemy import exc as sql_exception
from sys import dont_write_bytecode, path
from werkzeug.utils import secure_filename
from xlrd import open_workbook
from xlrd.biffh import XLRDError

dont_write_bytecode = True
path_app = dirname(abspath(__file__))
if path_app not in path:
    path.append(path_app)

from database import db, create_database
from models import City


def configure_database(app):
    create_database()

    @app.teardown_request
    def shutdown_session(exception=None):
        db.session.remove()
    db.init_app(app)


def configure_socket(app):
    async_mode = None
    socketio = SocketIO(app, async_mode=async_mode)
    thread_lock = Lock()
    return socketio


def import_cities():
    with open(join(path_app, 'data', 'cities.json')) as data:
        for city_dict in load(data):
            if int(city_dict['population']) < 500000:
                continue
            city = City(**city_dict)
            db.session.add(city)
        try:
            db.session.commit()
        except sql_exception.IntegrityError:
            db.session.rollback()

def create_app(config='config'):
    app = Flask(__name__)
    app.config.from_object('config')
    configure_database(app)
    socketio = configure_socket(app)
    from algorithms.pytsp import pyTSP
    tsp = pyTSP()
    import_cities()
    return app, socketio, tsp


app, socketio, tsp = create_app()


@app.route('/', methods=['GET', 'POST'])
def index():
    if 'file' in request.files:
        filename = request.files['file'].filename
        if allowed_file(filename, {'xls', 'xlsx'}):
            filename = secure_filename(filename)
            filepath = join(path_app, 'data', filename)
            request.files['file'].save(filepath)
            book = open_workbook(filepath)
            try:
                sheet = book.sheet_by_index(0)
            # if the sheet cannot be found, there's nothing to import
            except XLRDError:
                continue
            properties = sheet.row_values(0)
            for row_index in range(1, sheet.nrows):
                city_dict = dict(zip(properties, sheet.row_values(row_index)))
                city = City(**city_dict)
                db.session.add(city)
            db.session.commit()
    session['best'] = float('inf')
    session['crossover'], session['mutation'] = 'OC', 'Swap'
    creation_form = CreationForm(request.form)
    view = request.form['view'] if 'view' in request.form else '2D'
    cities = {
        city.id: {
            property: getattr(city, property)
            for property in City.properties
            }
        for city in City.query.all()
        }
    return render_template(
        'index.html',
        view=view,
        cities=cities,
        creation_form=creation_form,
        async_mode=socketio.async_mode
        )


@app.route('/<algorithm>', methods=['POST'])
def algorithm(algorithm):
    session['best'] = float('inf')
    return jsonify(*getattr(tsp, algorithm)(), False)


@socketio.on('genetic_algorithm')
def genetic_algorithm(data):
    if 'generation' not in session:
        session['generation'] = []
    session['generation'], best, length = tsp.cycle(session['generation'], **data)
    if length < session['best']:
        session['best'] = length
        emit('draw', ([best], [length], True))


if __name__ == '__main__':
    socketio.run(app)
