import requests
import os
from pprint import pprint

def get_data_fromlocal(ip,port,function,key): # 这个函数通过本地egolocal进程来跟远端通信（在不信任的环境下）
    resp = requests.post("http://{0}:{1}/{2}/{3}".format(ip,port,function,key)).json()
    if resp["errorflag"]:
        raise RuntimeError(resp["errormsg"])
    val = resp["val"]

    return val

def get_data_fromenv(server_ip,password,type,key): # 这个函数通过环境变量来跟远端通信（在信任的环境下）
    resp = requests.post("http://{0}/get/{1}/{2}".format(server_ip, type, key), json = {"password": password}).json()
    if resp["errorflag"]:
        raise RuntimeError(resp["errormsg"])
    val = resp["val"]
    return val

def get_data(port, local_ip, server_ip, password , type, key):
    server_ip = server_ip or os.environ.get("EGOROV_SERVER")
    password = password or os.environ.get("EGOROV_PWD")
    if server_ip and password: # 如果传入了server_ip和密码，或者这两个能从环境变量里找到，那就直接用这个跟远端通信。
        return get_data_fromenv(server_ip, password, type, key)

    # 跟本地进程通信
    function = "ask_{0}".format(type)
    return get_data_fromlocal(local_ip, port, function, key)


def get_variable(key, port = 11451, local_ip = "127.0.0.1", server_ip = None,password = None):
    return get_data(port, local_ip, server_ip, password , "variable", key)

def get_function(key, port = 11451, local_ip = "127.0.0.1", server_ip = None,password = None):
    return get_data(port, local_ip, server_ip, password , "function", key)


class _Egorov:
    def __init__(self):
        pass
    def __getitem__(self, key: str):
        try:
            return get_variable(key)
        except RuntimeError:
            return get_function(key)
Egorov = _Egorov()