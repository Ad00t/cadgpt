import json
import dotenv
import time
import os
import boto3
import logging
import sys

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

if os.environ['ENV'] == 'dev': 
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

lambda_client = boto3.client('lambda')
s3 = boto3.client('s3')

def lambda_handler(event, context):
    log_prefix = f'lambda_handler()'
    logger.info(f'{log_prefix}: {event} {context}')

    docs = []
    n_docs = int(event['n_docs'])
    for offset in range(0, n_docs, 20): 
        limit = min(20, n_docs - offset)
        logger.info(f'{log_prefix}: offset: {offset} limit: {limit}')
        
        search_obj = json.load(
            lambda_client.invoke(
                FunctionName='cadgpt-onshape-api',
                InvocationType='RequestResponse',  # synchronous
                Payload=json.dumps({
                    'endpoint': 'search_docs',
                    'payload': {
                        'query': event['query'],
                        'limit': limit,
                        'offset': offset
                    }
                }),
            )['Payload']
        )

        for item in search_obj.get('items', []):
            for hit in item.get('searchHits', []):
                if not (hit.get('elementtype') == 'PARTSTUDIO' or hit.get('elementType') == 'PARTSTUDIO'):
                    continue
                doc = {
                    'id': f"d:{hit['documentId']}:{hit['versionOrWorkspace']}:{hit['versionOrWorkspaceId']}:e:{hit['elementId']}",
                    'name': hit.get('name', '')
                }
                
                features_obj = json.load(
                    lambda_client.invoke(
                        FunctionName='cadgpt-onshape-api',
                        InvocationType='RequestResponse',  # synchronous
                        Payload=json.dumps({
                            'endpoint': 'get_features',
                            'payload': {
                                'doc_id': doc['id'],
                            }
                        }),
                    )['Payload']
                )

                features = features_obj.get('features', [])
                if len(features) > 0:
                    doc['features'] = features
                    docs.append(doc)

    ts = int(time.time_ns() / 1000000)
    N_DOCS_PER_FILE = int(os.environ['N_DOCS_PER_FILE'])
    for i in range(0, len(docs), N_DOCS_PER_FILE):
        put_doc_in_s3(
            key=f'docs_{ts}_{i}.json',
            body=json.dumps(docs[i:i+N_DOCS_PER_FILE])
        )         

    logger.info(f'{log_prefix}: done -- {len(docs)} docs found')
    return { 'statusCode': 200, 'body': json.dumps({ 'message': f'{len(docs)} docs found' }) }

def put_doc_in_s3(key, body):
    log_prefix = f'put_doc_in_s3({key}, {body})'
    try: 
        response = s3.put_object(Bucket='cadgpt-onshape-docs', Key=key, Body=body)
        logger.info(f'{log_prefix}: success') 
        logger.debug(response)
    except Exception as e:
        logger.error(f'{log_prefix} failed:', exc_info=True)
    return []

if os.environ['ENV'] == 'dev':
    dotenv.load_dotenv('.env')
    with open('test_event.json', 'r') as test_event_file:
        test_event = json.load(test_event_file)
        lambda_handler(test_event, {})
