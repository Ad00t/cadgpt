import json
import dotenv
import time
import os
import boto3
import logging
import sys

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if os.environ['ENV'] == 'dev':
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

lambda_client = boto3.client('lambda')

def lambda_handler(event, context):
    log_prefix = f'lambda_handler()'
    logger.info(f'{log_prefix}: {event} {context}')

    n_docs_found = 0
    n_docs = int(event['n_docs'])
    for offset in range(0, n_docs, 20):
        limit = min(20, n_docs - offset)
        logger.info(f'{log_prefix}: offset: {offset} limit: {limit}')

        search_obj = json.loads(json.load(
            lambda_client.invoke(
                FunctionName='cadgpt-onshape-api',
                InvocationType='RequestResponse',  # Synchronous
                Payload=json.dumps({
                    'endpoint': 'search_docs',
                    'payload': {
                        'query': event['query'],
                        'limit': limit,
                        'offset': offset
                    }
                }),
            )['Payload']
        )['body'])

        for item in search_obj.get('items', []):
            for hit in item.get('searchHits', []):
                if not (hit.get('elementtype') == 'PARTSTUDIO' or hit.get('elementType') == 'PARTSTUDIO'):
                    continue

                doc = {
                    'doc_id': f"d.{hit['documentId']}.{hit['versionOrWorkspace']}.{hit['versionOrWorkspaceId']}.e.{hit['elementId']}",
                    'doc_name': hit.get('name', '')
                }

                logger.info(f'{log_prefix}: invoking preprocessor: {doc}')
                response = lambda_client.invoke(
                    FunctionName='cadgpt-doc-preprocessor',
                    InvocationType='Event', # Asynchronous
                    Payload=json.dumps({ 'doc': doc })
                )
                n_docs_found += 1

    logger.info(f'{log_prefix}: done -- {n_docs_found} docs found')
    return { 'statusCode': 200, 'body': json.dumps({ 'message': f'{n_docs_found} docs found' }) }

if os.environ['ENV'] == 'dev':
    dotenv.load_dotenv('.env')
    with open('test_event.json', 'r') as test_event_file:
        test_event = json.load(test_event_file)
        lambda_handler(test_event, {})
