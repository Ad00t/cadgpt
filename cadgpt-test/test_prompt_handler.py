import requests
import dotenv
import os

if __name__ == '__main__':
    dotenv.load_dotenv('.env')
    response = requests.post(
        url=f"{os.environ['PROMPT_HANDLER_BASE_URL']}/prompt-handler", 
        headers={
            'x-api-key': os.environ['PROMPT_HANDLER_API_KEY'] 
        },
        json={ 
            'prompt': 'a cube with sides of length 10 cm',
            'doc_id': 'd:c86d2bb9b0b5645b6ef82963:w:c1f073b8750c1e629cd15c6b:e:6299020e04e494c4245b4a9c' 
        }
    )
    print(f'Response: {response.status_code} {response.text}')
