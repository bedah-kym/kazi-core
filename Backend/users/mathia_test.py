
import json
import requests
"""
url ="http://127.0.0.1:8000/chatbot/home/3/"
r = requests.post(url,data= ({
                'message': "message",
                'from':"test2",
                'command':'new_message',
                "chatid":3
            }))

def auth():
    endpoint = "http://127.0.0.1:8000/auth/"
    response = requests.post(endpoint,json={"username":"test2","password":"G3pSkCtu3FZ6fEj"})
    if response.status_code == 200 :
        token=response
        return token.json()# {'token': '35fd13777168eb84e0e41413fd3f7cda55e7fe3b'} {regular:'b0676d1646866d32709fee2c21cc657720dab4b6'}
    return response.status_code
"""

def get():
    #token=auth()
    strtoken='1e51e8f61c30893852b4e42aac3bb252aa24bee0'
    endpoint = "http://127.0.0.1:8000/api/getreplies/"
    headers={"Authorization":f"token {strtoken}"}
    response = requests.get(endpoint,headers=headers)
    return response.json()

print(get())