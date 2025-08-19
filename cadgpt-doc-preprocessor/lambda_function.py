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

lambda_client = boto3.client('lambda')
ssm = boto3.client('ssm')

openai_client: OpenAI | None = None
vector_store: QdrantClient | None = None
doc_db: Database | None = None

def lambda_handler(event, context):
    log_prefix = f'lambda_handler()'
    logger.info(f'{log_prefix} {json.dumps(event)} {context}')

    init_clients()
    if openai_client is None or vector_store is None or doc_db is None:
        logger.error(f'{log_prefix}: clients not initialized')
        return { 'statusCode': 500, 'body': json.dumps({ 'message': 'clients not initialized' })}

    try:
        doc = event['doc']
        doc_id = doc['doc_id']
        doc_name = doc['doc_name'].strip()
        new_id = str(uuid.uuid4()) 

        if (check_doc_id_exists(doc_id)):
            logger.info(f'{log_prefix}: doc {doc_id} already exists')
            return { 'statusCode': 400, 'body': json.dumps({ 'message': 'doc already exists' }) }

        features = get_features(doc_id)
        if len(features) == 0:
            logger.info(f'{log_prefix}: no features found for doc {doc_id}')
            return { 'statusCode': 400, 'body': json.dumps({ 'message': 'empty features list' }) }

        for feature in features:
            if feature['featureType'] == 'importForeign':
                logger.info(f'{log_prefix}: skipping, import detected in doc {doc_id}')
                return { 'statusCode': 400, 'body': json.dumps({ 'message': 'import detected' }) }

        desc = generate_desc(doc_name, features)
        if desc is None:
            logger.info(f'{log_prefix}: failed to generate desc for doc {doc_id}')
            return { 'statusCode': 500, 'body': json.dumps({ 'message': 'failed to generate desc' }) }

        metadata = {
            'doc_id': doc_id,
            'name': doc_name,
            'desc': desc,
            'collection': 'partstudio_features'
        }

        insert_dbs(new_id, desc, metadata, features)
    except Exception as e:
        logger.error(f'{log_prefix}: failed:', exc_info=True)
       
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

def get_features(doc_id):
    features_obj = json.loads(json.load(
        lambda_client.invoke(
            FunctionName='cadgpt-onshape-api',
            InvocationType='RequestResponse',  # Synchronous
            Payload=json.dumps({
                'endpoint': 'get_features',
                'payload': {
                    'doc_id': doc_id,
                }
            }),
        )['Payload']
    )['body'])
    features = features_obj.get('features', [])
    return features

def generate_desc(doc_name, features):
    log_prefix = f'generate_desc()'
    global openai_client
    logger.info(f"{log_prefix}: llm: {os.environ['LLM']}")

    with open('llm_static/instructions_template.txt', 'r') as template_file:
        template = template_file.read()
    
    instructions = template.format(
        doc_name=doc_name,
        features=json.dumps(features)
    )

    response = openai_client.responses.create(
        model=os.environ['LLM'],
        input=[
            { 'role': 'developer', 'content': instructions }
        ]
    )

    desc = f"{response.output_text}"
    logger.info(f'{log_prefix}: {desc}')
    return desc

def check_doc_id_exists(doc_id):
    log_prefix = f'check_doc_id_exists()'
    global doc_db
    doc = doc_db['docs'].find_one({ 'metadata.doc_id': doc_id })
    logger.info(f'{log_prefix}: {doc}')
    return doc is not None

def insert_dbs(new_id, desc, metadata, features):
    log_prefix = f'insert_dbs()'
    global vector_store, doc_db

    desc_vec = openai_client.embeddings.create(
        input=desc,
        model=os.environ['EMBEDDING_MODEL']
    ).data[0].embedding

    point = PointStruct(
        id=new_id,
        vector=desc_vec,
        payload=metadata
    )

    vs_response = vector_store.upsert(
        collection_name=metadata['collection'],
        wait=True,
        points=[ point ]
    )
    logger.info(f'{log_prefix}: vs response: {vs_response}')

    ddb_response = doc_db[metadata['collection']].insert_one({
        '__id': new_id,
        'metadata': metadata,
        'features': features
    })
    logger.info(f'{log_prefix}: ddb response: {ddb_response}')

if os.environ['ENV'] == 'dev':
    dotenv.load_dotenv('.env')
    with open('test_event.json', 'r') as test_event_file:
        test_event = json.load(test_event_file)
        lambda_handler(test_event, {})
