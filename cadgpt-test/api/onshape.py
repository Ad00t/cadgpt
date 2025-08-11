import os
import base64
import requests

headers = {
    'Accept': 'application/json;charset=UTF-8; qs=0.09',
    'Content-Type': 'application/json',
    'Authorization': '' 
}

did = 'c86d2bb9b0b5645b6ef82963'
wvm = 'w'
wvmid = 'c1f073b8750c1e629cd15c6b'
eid = '6299020e04e494c4245b4a9c'
url_base = f'https://cad.onshape.com/api/v9/partstudios/d/{did}/{wvm}/{wvmid}/e/{eid}/features'

def init(auth_type='basic'):
    if auth_type == 'basic':
        key = f"{os.getenv('ONSHAPE_API_KEY')}:{os.getenv('ONSHAPE_API_SECRET')}".encode('utf-8')
        headers['Authorization'] = f"Basic {base64.b64encode(key).decode('utf-8')}"
    print('Authenticated with OnShape')

def get_features():
    response = requests.get(
        url_base,
        headers=headers,
        params={
            'rollbackBarIndex': -1,
            'includeGeometryIds': 'true',
            'noSketchGeometry': 'false'
        }
    )
    print('onshape.get_features():', response.status_code, response.text, '\n')
    return response

def add_feature(body):
    response = requests.post(
        url_base,
        headers=headers,
        json=body
    )
    print('onshape.add_feature():', response.status_code, response.text, '\n')
    return response 

def update_feature(fid, body):
    response = requests.post(
        f'{url_base}/featureid/{fid}',
        headers=headers,
        json=body
    )
    print('onshape.update_feature():', response.status_code, response.text, '\n')
    return response 

def delete_feature(fid):
    response = requests.delete(
        f'{url_base}/featureid/{fid}',
        headers=headers,
    )
    print('onshape.delete_feature():', response.status_code, response.text, '\n')
    return response 
