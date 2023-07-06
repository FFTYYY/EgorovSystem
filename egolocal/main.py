from YTools.network.server.server import start_server
import os
import sys
import argparse
import urllib.request
import urllib.parse
import time
import pdb
import requests
import json
from functools import partial

def ask_ip(): # 这个函数向环境变量中询问远端的地址
    server_ip = os.environ.get("EGOROV_SERVER")
    if server_ip is None:
        server_ip = input("server ip = ")
    return server_ip.strip()

def ask_password(): # 这个函数询问密码
    if os.environ.get("EGOROV_PWD"): # 只在个人计算机上存密码
        return os.environ["EGOROV_PWD"]
    return input("password = ").strip()

def get_data(server_ip, password, key, type):
    return requests.post("http://{0}/get/{1}/{2}".format(server_ip, type, key), json = {"password": password}).json()

def run():
    # os.chdir(os.path.dirname(__file__))
    # sys.path.append(os.path.abspath("."))
    
    server_ip = ask_ip() # 获得远端地址
    password = ask_password()

    C = argparse.ArgumentParser()
    C.add_argument("--port" , type = int , default = 11451)
    C = C.parse_args()

    def ask_variable(request, key):
        return get_data(server_ip, password, key, "variable")
    def ask_function(request, key):
        return get_data(server_ip, password, key, "function")
    def hello(request):
        return "hello"
    start_server(ip = "127.0.0.1" , port = C.port , responsers = {
        "hello":  hello, 
        "ask_variable/<str:key>":  ask_variable, 
        "ask_function/<str:key>":  ask_function, 
    } , encode = "json" , cross_domain = True)

if __name__ == "__main__":
    run()