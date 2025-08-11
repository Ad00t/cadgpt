import json
import boto3
import logging
import os
import sys
import dotenv

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
    logger.info(f'{log_prefix}: {json.dumps(event)}, {context}')
    
    response = lambda_client.invoke(
        FunctionName='cadgpt-prompt-handler',
        InvocationType='Event',
        Payload=json.dumps(event)
    )
    logger.info(f'{log_prefix}: {response}')

    return { 'statusCode': 200, 'body': json.dumps({ 'message': 'request acknowledged' }) }

if os.environ['ENV'] == 'dev':
    dotenv.load_dotenv('.env')
    with open('test_event.json', 'r') as test_event_file:
        test_event = json.load(test_event_file)
        lambda_handler(test_event, {})
