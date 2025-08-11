import json
import logging
import boto3
import os
import sys
import uuid
import dotenv
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.http.models import PointStruct
from pymongo.synchronous.database import Database
from pymongo import MongoClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if os.environ['ENV'] == 'dev': 
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

s3 = boto3.client('s3')
ssm = boto3.client('ssm')

openai_client: OpenAI | None = None
vector_store: QdrantClient | None = None
doc_db: Database | None = None

def lambda_handler(event, context):
    log_prefix = f'lambda_handler()'
    logger.info(f'{log_prefix} {json.dumps(event)} {context}')

    init_clients()
    if openai_client is None or vector_store is None or doc_db is None:
        logger.error(f'{log_prefix}: clients not initialized {str(openai_client)} {str(vector_store)} {str(doc_db)}')
        return { 'statusCode': 500, 'body': json.dumps({ 'message': 'clients not initialized' })}

    raw_obj = retrieve_s3_object(event)

    # TODO: input validation
    # TODO: enforce atomicity of all operations

    for obj in raw_obj:
        try:
            doc = f'Name: {obj.get('name', '')}'
            doc_id = obj['id']
            vs_id = str(uuid.uuid4()) 

            doc_vec = openai_client.embeddings.create(
                input=doc,
                model=os.environ['EMBEDDING_MODEL']
            ).data[0].embedding

            point = PointStruct(
                id=vs_id,
                vector=doc_vec,
                payload={
                    'doc_id': doc_id,
                    'type': 'partstudio_features' 
                }
            )

            vector_store_response = vector_store.upsert(
                collection_name='docs',
                wait=True,
                points=[ point ]
            )
            logger.info(f'{log_prefix}: vector_store response: {vector_store_response}')

            doc_db_response = doc_db['docs'].insert_one({
                '__id': doc_id,
                'metadata': {
                    'vs_id': vs_id,
                    'type': 'partstudio_features'
                },
                'features': obj.get('features', {})
            })
            logger.info(f'{log_prefix}: doc_db response: {doc_db_response}')
        except Exception as e:
            logger.error(f'{log_prefix}: processing doc {obj.get('id', 'NO_ID')} failed: ', exc_info=True)
       
    logger.info(f'{log_prefix}: done')
    return { 'statusCode': 200, 'body': json.dumps({ 'message': 'success' }) }

def init_clients():
    log_prefix = f'init_clients()'
    global openai_client, vector_store, doc_db
    if not (openai_client is None or vector_store is None or doc_db is None):
        logger.info(f'{log_prefix}: clients already initialized')
        return
    
    os.environ['OPENAI_API_KEY'] = ssm.get_parameter(Name='OPENAI_API_KEY', WithDecryption=True)['Parameter']['Value']
    os.environ['QDRANT_API_KEY'] = ssm.get_parameter(Name='QDRANT_API_KEY', WithDecryption=True)['Parameter']['Value']
    os.environ['CADGPT_DOC_DB_PASS'] = ssm.get_parameter(Name='CADGPT_DOC_DB_PASS', WithDecryption=True)['Parameter']['Value'] 

    logger.info(f'{log_prefix}: credentials retrieved')

    openai_client = OpenAI(
        api_key=os.environ['OPENAI_API_KEY']
    )
    logger.info(f'{log_prefix}: OpenAI client initialized')

    vector_store = QdrantClient(
        url=os.environ['VECTOR_STORE_URL'],
        api_key=os.environ['QDRANT_API_KEY'],
    )
    logger.info(f'{log_prefix}: vector store initialized')

    doc_db = MongoClient(
        host=f"mongodb://{os.environ['DOC_DB_USER']}:{os.environ['CADGPT_DOC_DB_PASS']}@{os.environ['DOC_DB_HOST']}:{os.environ['DOC_DB_PORT']}/?ssl=true&retryWrites=false",
        tls=True,   
        tlsCAFile='global-bundle.pem'
    )[os.environ['DOC_DB_NAME']]      
    logger.info(f'{log_prefix}: {doc_db.name} initialized')

def retrieve_s3_object(event) -> list:
    log_prefix = f'retrieve_s3_object()'
    try:
        record = event['Records'][0]
        bucket_name = record['s3']['bucket']['name']
        key = record['s3']['object']['key']
        logger.info(f'{log_prefix}: {bucket_name}/{key} {event}') 

        response = s3.get_object(Bucket=bucket_name, Key=key)
        content = response['Body'].read().decode('utf-8')
        logger.info(f'{log_prefix}: success')
        return json.loads(content)
    except Exception as e:
        logger.error(f'{log_prefix} failed:', exc_info=True)
    return []

if os.environ['ENV'] == 'dev':
    dotenv.load_dotenv('.env')
    with open('test_event.json', 'r') as test_event_file:
        test_event = json.load(test_event_file)
        lambda_handler(test_event, {})
