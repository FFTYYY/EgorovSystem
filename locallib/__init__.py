import requests

def get_data(ip,port,function,arg):

    resp = requests.post("http://{0}:{1}/{2}/{3}".format(ip,port,function,arg)).json()
    if resp["errorflag"]:
        raise Exception(resp["errormessage"])
    val = resp["val"]

    return val

def get_variable(key, port = 11451, ip = "127.0.0.1"):
    return get_data(ip,port,"ask_variable",key)

def get_function(key, port = 11451, ip = "127.0.0.1"):
    return get_data(ip,port,"ask_function",key)
