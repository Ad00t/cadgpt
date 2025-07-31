import json
import logging
import boto3
import os
import sys
import dotenv
from openai import OpenAI
from qdrant_client import QdrantClient
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

llm_static_context: str | None = None
feature_list_detailed_schema: dict | None = None
feature_definition_detailed_schema: dict | None = None

def lambda_handler(event, context):
    log_prefix = f'lambda_handler()'
    logger.info(f'{log_prefix} {json.dumps(event)} {context}')

    init_clients()
    if openai_client is None or vector_store is None or doc_db is None:
        logger.error(f'{log_prefix}: clients not initialized {str(openai_client)} {str(vector_store)} {str(doc_db)}')
        return { 'statusCode': 500, 'body': json.dumps({ 'message': 'clients not initialized' })}

    try:
        body = json.loads(event['body'])
        prompt = body['prompt'].strip()
        doc_id = body['doc_id'].strip()
        logger.info(f'{log_prefix}: prompt: "{prompt}" doc_id: "{doc_id}"')       
    except Exception as e: 
        logger.error(f'{log_prefix}: prompt error: ', exc_info=True)
        return { 'statusCode': 400, 'body': json.dumps({ 'message': 'bad prompt' }) }

    generate_static_context()
    full_context = generate_full_rag_context(prompt=prompt)
    llm_response = generate_llm_response(context=full_context, prompt=prompt, doc_id=doc_id)
    
    logger.info(f'{log_prefix}: done')
    return { 'statusCode': 200, 'body': json.dumps(llm_response) }

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
    logger.info(f'{log_prefix}: llm initialized')

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

def generate_static_context():
    log_prefix = f'generate_static_context()'
    global llm_static_context, feature_list_detailed_schema, feature_definition_detailed_schema
    if not (llm_static_context is None or feature_list_detailed_schema is None or feature_definition_detailed_schema is None):
        logger.info(f'{log_prefix}: static context already exists')
        return

    with open('llm_static/llm_instructions.txt') as instr_txt:
        llm_static_context = instr_txt.read()

    with open('llm_static/features_api_doc.txt') as api_doc_txt:
        llm_static_context = llm_static_context.replace('${features_api_doc.txt}', api_doc_txt.read())


def generate_full_rag_context(prompt) -> str:
    log_prefix = f'generate_full_rag_context()'
    try:
        global openai_client, vector_store, llm_static_context
        if llm_static_context is None:
            logger.error(f'{log_prefix}: llm_static_context not initialized')
            return '' 
        if openai_client is None or vector_store is None or doc_db is None:
            logger.error(f'{log_prefix}: clients not initialized {str(openai_client)} {str(vector_store)} {str(doc_db)}')
            return llm_static_context

        prompt_vec = openai_client.embeddings.create(
            input=prompt,
            model=os.environ['EMBEDDING_MODEL']
        ).data[0].embedding

        search_result = vector_store.search(
            collection_name='docs',
            query_vector=prompt_vec,
            limit=int(os.environ['MAX_CONTEXT_EXAMPLES'])
        )
        logger.info(f'{log_prefix}: RAG result:\n\n{search_result}')
        
        examples_text = ''
        for i, rag_point in enumerate(search_result):
            try:
                if rag_point.payload is None:
                    logger.error(f'{log_prefix}: rag_point has no payload: {rag_point}')
                    continue
                rag_doc = doc_db['docs'].find_one({ '__id': rag_point.payload['doc_id'] })
                examples_text += f'### EXAMPLE {i+1}\n\n{rag_doc}\n\n'
            except Exception as e:
                logger.error(f'{log_prefix}: RAG document retrieval failed: ', exc_info=True)
        llm_full_context = llm_static_context.replace('${feature_list_examples}', examples_text)
        return llm_full_context
    except Exception as e:
        logger.error(f'{log_prefix}: failed: ', exc_info=True)
    return ''

def generate_llm_response(context, prompt, doc_id):
    log_prefix = f'generate_llm_response()'
    if openai_client is None:
        return []

    with open('llm_static/final_schema.json') as in_file:
        schema = json.load(in_file)
        logger.debug(f'{log_prefix}: schema loaded:\n\n{json.dumps(schema)}')

    logger.info(f'{log_prefix}: context: ~{len(context.split(' '))} tokens')
    logger.debug(f'{log_prefix}: {context}')

    for step in range(int(os.environ['MAX_FEATURES'])):
        try:
            curr_features = json.loads(json.load(
                lambda_client.invoke(
                    FunctionName='cadgpt-onshape-api',
                    InvocationType='RequestResponse',  # synchronous
                    Payload=json.dumps({
                        'endpoint': 'get_features',
                        'payload': {
                            'doc_id': doc_id, 
                        }
                    }),
                )['Payload']
            )['body'])['features']
            logger.debug(f'{log_prefix}: curr features: {json.dumps(curr_features)}')
        
            response = openai_client.responses.create(
                model=os.environ['OPENAI_LLM'],
                input=[
                    { 'role': 'system', 'content': context },
                    { 'role': 'user', 'content': f'Existing features:\n\n{json.dumps(curr_features, indent=4)}\n' },
                    { 'role': 'user', 'content': f'Generate the next feature object in this design given the user prompt: {prompt}' }
                ],
                text={
                    'format': {
                        'type': 'json_schema',
                        'name': 'feature_definition',
                        'strict': True,
                        'schema': schema 
                    }
                }
            )
            logger.info(f'{log_prefix} step {step}: {response}')
            response_json = json.loads(response.output_text)
            
            add_feature_response = json.load(
                lambda_client.invoke(
                    FunctionName='cadgpt-onshape-api',
                    InvocationType='RequestResponse',  # synchronous
                    Payload=json.dumps({
                        'endpoint': 'add_feature',
                        'payload': {
                            'doc_id': doc_id,
                            'feature': response_json['feature']
                        }
                    }),
                )['Payload']
            )
            logger.info(f"{log_prefix}: add_feature: {add_feature_response['statusCode']} {add_feature_response['body']}")

            if response_json['metadata']['done']:
                break
        except Exception as e:
            logger.error(f'{log_prefix}: failed: ', exc_info=True)
            break

if os.environ['ENV'] == 'dev':
    dotenv.load_dotenv('.env')
    with open('test_event.json', 'r') as test_event_file:
        test_event = json.load(test_event_file)
        lambda_handler(test_event, {})
