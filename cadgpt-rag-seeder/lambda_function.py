import json
import logging
import boto3
import os
import sys
import dotenv
from openai import OpenAI

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if os.environ['ENV'] == 'dev':
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

lambda_client = boto3.client('lambda')
ssm = boto3.client('ssm')

openai_client: OpenAI | None = None

def lambda_handler(event, context):
    log_prefix = f'lambda_handler()'
    logger.info(f'{log_prefix} {json.dumps(event)} {context}')

    init_clients()
    if openai_client is None:
        logger.error(f'{log_prefix}: clients not initialized')
        return { 'statusCode': 500, 'body': json.dumps({ 'message': 'clients not initialized' })}

    queries = generate_queries(event['n_queries'])
    for query in queries:
        onshape_doc_search(query, event['n_docs_per_query'])

    logger.info(f'{log_prefix}: done')
    return { 'statusCode': 200, 'body': json.dumps({ 'message': 'success' }) }

def init_clients():
    log_prefix = f'init_clients()'
    global openai_client
    if not (openai_client is None):
        logger.info(f'{log_prefix}: clients already initialized')
        return

    os.environ['OPENAI_API_KEY'] = ssm.get_parameter(Name='OPENAI_API_KEY', WithDecryption=True)['Parameter']['Value']
    logger.info(f'{log_prefix}: credentials retrieved')

    openai_client = OpenAI(
        api_key=os.environ['OPENAI_API_KEY']
    )
    logger.info(f'{log_prefix}: OpenAI client initialized')

def generate_queries(n_queries):
    log_prefix = f'generate_desc()'
    global openai_client
    logger.info(f'{log_prefix}: llm: {os.environ['LLM']}')

    with open('llm_static/instructions_template.txt', 'r') as template_file, \
         open('llm_static/query_examples.txt', 'r') as examples_file:
        template = template_file.read()
        query_examples = examples_file.read()

    instructions = template.format(
        n_queries=n_queries,
        query_examples=query_examples
    )
    logger.info(f'{log_prefix}: instructions loaded')
    logger.debug(f'{log_prefix}: {instructions}')

    response = openai_client.responses.create(
        model=os.environ['LLM'],
        input=[
            { 'role': 'developer', 'content': instructions }
        ]
    )

    queries = response.output_text.split('\n')
    logger.info(f'{log_prefix}: {json.dumps(queries)}')
    return queries

def onshape_doc_search(query, n_docs_per_query):
    log_prefix = f'onshape_doc_search()'
    try:
        payload = json.dumps({
            'query': query,
            'n_docs': n_docs_per_query
        })
        lambda_client.invoke(
            FunctionName='cadgpt-onshape-doc-search',
            InvocationType='Event', # Asynchronous
            Payload=payload
        )
        logger.info(f'{log_prefix}: lambda invoked -- {payload}')
    except Exception as e:
        logger.error(f'{log_prefix}: failed: ', exc_info=True)

if os.environ['ENV'] == 'dev':
    dotenv.load_dotenv('.env')
    with open('test_event.json', 'r') as test_event_file:
        test_event = json.load(test_event_file)
        lambda_handler(test_event, {})
