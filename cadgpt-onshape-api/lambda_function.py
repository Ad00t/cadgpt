import json
import os
import boto3
import base64
import requests
import logging
import sys
import dotenv

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

if os.environ['ENV'] == 'dev': 
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

headers = {
    'Accept': 'application/json;charset=UTF-8; qs=0.09',
    'Content-Type': 'application/json',
    'Authorization': '' 
}

ssm = boto3.client('ssm')

def lambda_handler(event, context):
    log_prefix = f'lambda_handler()'
    logger.info(f'{log_prefix}: {event} {context}')
    
    authenticate()

    match event['endpoint']:
        case 'search_docs':
            return make_request(event, 
                lambda payload: requests.post(
                    f"{os.environ['URL_BASE']}/documents/search",
                    headers=headers,
                    json={
                        "documentFilter": 4, # 0: My Documents | 1: Created | 2: Shared | 3: Trash | 4: Public | 5: Recent | 6: By Owner | 7: By Company | 9: By Team
                        "foundIn": "ALL",
                        "limit": payload['limit'],
                        "offset": payload['offset'],
                        # "ownerId": "string",
                        # "parentId": "ALL",
                        "rawQuery": payload['query'],
                        "sortColumn": "createdAt",
                        "sortOrder": "desc",
                        "type": "element",
                        "when": "ALL"
                    }
                )
            )
        case 'get_features':
            return make_request(event,
                lambda payload: requests.get(
                    f"{os.environ['URL_BASE']}/partstudios/{payload['doc_id'].replace(':', '/')}/features",
                    headers=headers,
                    params={
                        'rollbackBarIndex': -1,
                        'includeGeometryIds': True,
                        'noSketchGeometry': False
                    }
                )
            )
        case 'add_feature':
            return make_request(event,
                lambda payload: requests.post(
                    f"{os.environ['URL_BASE']}/partstudios/{payload['doc_id'].replace(':', '/')}/features",
                    headers=headers,
                    json={
                        'btType': 'BTFeatureDefinitionCall-1406',
                        'feature': payload['feature']
                    }
                )
            )
        case 'update_feature':
            return make_request(event,
                lambda payload: requests.post(
                    f"{os.environ['URL_BASE']}/partstudios/{payload['doc_id'].replace(':', '/')}/features/featureid/{payload['feature_id']}",
                    headers=headers,
                    json={
                        'btType': 'BTFeatureDefinitionCall-1406',
                        'feature': payload['feature']
                    }
                )
            )
        case 'delete_feature':
            return make_request(event,
                lambda payload: requests.delete(
                    f"{os.environ['URL_BASE']}/partstudios/{payload['doc_id'].replace(':', '/')}/features/featureid/{payload['feature_id']}",
                    headers=headers
                )
            )

    return { 'statusCode': 400, 'body': json.dumps({ 'message': 'endpoint not found' }) }

def authenticate() -> None:
    log_level = f'authenticate()'
    global headers
    if headers['Authorization'] != '':
        logger.info(f'{log_level}: already authenticated')
        return

    os.environ['ONSHAPE_API_KEY'] = ssm.get_parameter(Name='ONSHAPE_API_KEY', WithDecryption=True)['Parameter']['Value']
    os.environ['ONSHAPE_API_SECRET'] = ssm.get_parameter(Name='ONSHAPE_API_SECRET', WithDecryption=True)['Parameter']['Value']
   
    # TODO: replace with oauth
    key = f"{os.environ['ONSHAPE_API_KEY']}:{os.environ['ONSHAPE_API_SECRET']}".encode('utf-8')
    headers['Authorization'] = f"Basic {base64.b64encode(key).decode('utf-8')}"

    logger.info(f'{log_level}: authenticated')

def make_request(event, request_func):
    log_prefix = f"Endpoint {event['endpoint']}" 
    payload = event['payload']
    try:
        response = request_func(payload)
        logger.info(f'{log_prefix}: {response.status_code}')
        logger.debug(f'{log_prefix}: {response.text}')
        return { 'statusCode': response.status_code, 'body': response.text }
    except Exception as e:
        logger.error(f'{log_prefix} failed:', exc_info=True)
    return { 'statusCode': 500, 'body': json.dumps({ 'message': 'something went wrong' }) }

if os.environ['ENV'] == 'dev':
    dotenv.load_dotenv('.env')
    with open('test_event.json', 'r') as test_event_file:
        test_event = json.load(test_event_file)
        lambda_handler(test_event, {})
