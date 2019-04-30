#!/usr/bin/env python
# -*- coding: utf-8 -*-

import shutil
import uuid
import time
import json

from flask import render_template, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from datetime import datetime
from flask_cors import CORS
from utils import *

app.config.from_object('config')
CORS(app, resources=r'/*')


@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload():
    # get upload files
    file1 = request.files['file1']
    file2 = request.files['file2']
    # generate client id and create folder
    client_id = str(uuid.uuid1())
    os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], client_id))
    f_type = []
    for file in [file1, file2]:
        filename = secure_filename(file.filename)
        # convert excel to dataframe
        if filename.endswith('.xlsx') or filename.endswith('xls'):
            dataframe = pd.read_excel(file)
        else:
            dataframe = pd.read_csv(file)
        # handling column name exception
        if check_column(dataframe):
            error = check_column(dataframe)
            return jsonify(error), 400
        # save formatted dataframe
        if 'Confirmed' not in dataframe.columns:
            dataframe = format_data(dataframe)
        save_data(dataframe, client_id)
        # store file types by flag
        f_flag = dataframe.shape[1] < app.config['DISTINCT_NUM']
        f_type.append(f_flag)
    # handling file type exception
    if check_type(f_type):
        error = check_type(f_type)
        return jsonify(error), 400
    # construct json for frontend
    res = dict()
    res['client_id'] = client_id
    alarm, confirmed_num, accuracy = result_monitor(client_id)
    res['start'] = pd.to_datetime(alarm['First'].min()).timestamp()
    res['end'] = pd.to_datetime(alarm['First'].max()).timestamp()
    res['accuracy'] = accuracy
    res['total_alarm'] = alarm.shape[0]
    res['p_count'] = alarm.loc[alarm['RcaResult_Edited'] == 'P'].shape[0]
    res['c_count'] = alarm.loc[alarm['RcaResult_Edited'] == 'C'].shape[0]
    res['x_count'] = alarm.loc[alarm['RcaResult_Edited'] == ''].shape[0]
    res['group_count'] = len(set(alarm['GroupId_Edited'].dropna()))
    res['confirmed'] = confirmed_num
    res['unconfirmed'] = res['group_count'] - res['confirmed']
    return jsonify(res)


@app.route('/interval', methods=['GET'])
def interval():
    # get interval filtered dataframe
    a_time = datetime.fromtimestamp(int(request.args.get('start')))
    z_time = datetime.fromtimestamp(int(request.args.get('end')))
    alarm = interval_limit(a_time, z_time)
    # construct json for frontend
    res = dict()
    res['group_id'] = list(set(alarm['GroupId']))
    return jsonify(res)


@app.route('/analyze', methods=['GET'])
def analyze():
    # generate topo tree
    group_id = request.args.get('groupId')
    alarm = group_filter(group_id)
    topo_path = find_path(set(alarm['AlarmSource']))
    topo_tree = build_tree(topo_path)
    # construct json for frontend
    res = dict()
    res['topo'] = topo_tree
    res['table'] = json.loads(alarm.to_json(orient='records'))
    res['orange'] = list(set(alarm['AlarmSource']))
    return jsonify(res)


@app.route('/expand', methods=['GET'])
def expand():
    # generate topo path
    group_id = request.args.get('groupId')
    add_time = int(request.args.get('addTime'))
    alarm = group_filter(group_id)
    topo_path = find_path(set(alarm['AlarmSource']))
    topo_ne = path2ne(topo_path)
    # get interval filtered dataframe
    a_time = datetime.fromtimestamp(pd.to_datetime(alarm['First'].min())
                                    .timestamp() - add_time * 60 - 8 * 60 * 60)
    z_time = datetime.fromtimestamp(pd.to_datetime(alarm['First'].max())
                                    .timestamp() + add_time * 60 - 8 * 60 * 60)
    alarm = interval_limit(a_time, z_time)
    # check intersection and join the tree
    alarm = alarm.loc[alarm['GroupId_Edited'] == '']
    extra_path = find_path(set(alarm['AlarmSource']))
    for path in extra_path:
        extra_ne = path2ne(path)
        if extra_ne & topo_ne:
            topo_path = topo_path | path
    topo_tree = build_tree(topo_path)
    # construct json for frontend
    res = dict()
    res['topo'] = topo_tree
    res['table'] = json.loads(alarm.to_json(orient='records'))
    alarm = alarm.loc[alarm['GroupId'] != group_id]
    res['yellow'] = list(set(alarm['AlarmSource']))
    return jsonify(res)


@app.route('/confirm', methods=['POST'])
def confirm():
    client_id = request.headers.get('Client-Id')
    alarm = pd.read_csv(os.path.join(app.config['UPLOAD_FOLDER'], client_id,
                                     app.config['ALARM_FILE']))
    # get edited information
    req = request.get_json()
    row_edited = req['row']
    columns_edited = req['columns']
    values_edited = req['values']
    # save confirmed data
    for row, columns, values in zip(row_edited, columns_edited, values_edited):
        mask = alarm['Index'] == row
        # edit each row
        for column, value in zip(columns, values):
            alarm.loc[mask, column] = value
        # fill confirmed field
        if alarm.loc[mask, 'GroupId_Edited'].any():
            alarm.loc[mask, 'Confirmed'] = '1'
        else:
            alarm.loc[mask, 'Confirmed'] = ''
    save_data(alarm, client_id)
    # construct json for frontend
    res = dict()
    alarm, confirmed_num, accuracy = result_monitor(client_id)
    res['accuracy'] = accuracy
    res['total_alarm'] = alarm.shape[0]
    res['p_count'] = alarm.loc[alarm['RcaResult_Edited'] == 'P'].shape[0]
    res['c_count'] = alarm.loc[alarm['RcaResult_Edited'] == 'C'].shape[0]
    res['x_count'] = alarm.loc[alarm['RcaResult_Edited'] == ''].shape[0]
    res['group_count'] = len(set(alarm['GroupId_Edited'].dropna()))
    res['confirmed'] = confirmed_num
    res['unconfirmed'] = res['group_count'] - res['confirmed']
    return jsonify(res)


@app.route('/detail', methods=['GET'])
def detail():
    client_id = request.headers.get('Client-Id')
    alarm = pd.read_csv(os.path.join(app.config['UPLOAD_FOLDER'], client_id,
                                     app.config['ALARM_FILE']))
    # get confirmed/unconfirmed groups
    confirmed_group = []
    unconfirmed_group = []
    for group_id in set(alarm['GroupId']):
        mask = alarm['GroupId_Edited'] == group_id
        if alarm.loc[mask].shape[0] == alarm.loc[mask]['Confirmed'].count():
            confirmed_group.append(group_id)
        else:
            unconfirmed_group.append(group_id)
    # construct json for frontend
    res = dict()
    res['confirmed'] = confirmed_group
    res['unconfirmed'] = unconfirmed_group
    return jsonify(res)


@app.route('/download', methods=['GET'])
def download():
    # get directory path
    client_id = request.args.get('clientId')
    dirpath = os.path.join(app.config['UPLOAD_FOLDER'], client_id)
    # generate file name
    filename = 'verified_alarm_' + str(int(time.time())) + '.csv'
    return send_from_directory(dirpath, 'alarm_format.csv', as_attachment=True,
                               attachment_filename=filename)


@app.route('/clean', methods=['POST'])
def clean():
    for dirname in os.listdir(app.config['UPLOAD_FOLDER']):
        # get directory path
        dirpath = os.path.join(app.config['UPLOAD_FOLDER'], dirname)
        # clean up cache regularly
        diff = time.time() - os.path.getmtime(dirpath)
        if diff > 7 * 24 * 60 * 60:
            shutil.rmtree(dirpath)


@app.errorhandler(500)
def error_500(exception):
    # construct json for frontend
    error = dict()
    error['code'] = 500
    error['message'] = '500 INTERNAL SERVER ERROR'
    return jsonify(error), 500
