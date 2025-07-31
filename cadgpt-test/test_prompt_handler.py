import requests
import json

BASE_URL = 'https://lmt3yqa7uh.execute-api.us-west-2.amazonaws.com/dev'
API_KEY = 't4hwon7axc4bA11ySehIR4UN6QxgWVcA7PztIZWE'

if __name__ == '__main__':
    response = requests.post(
        url=f'{BASE_URL}/prompt-handler', 
        headers={
            'x-api-key': API_KEY 
        },
        json={ 
            'prompt': 'a cube with sides of length 10 cm',
            'doc_id': 'd:c86d2bb9b0b5645b6ef82963:w:c1f073b8750c1e629cd15c6b:e:6299020e04e494c4245b4a9c' 
        }
    )
    print(f'Response: {response.status_code} {response.text}')
