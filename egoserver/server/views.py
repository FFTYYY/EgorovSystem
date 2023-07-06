from django.http import HttpResponse , JsonResponse , Http404
import json
from .models import Variable, Function, Password
import pdb
from .utils import JSONDecode
import random

def check_password(request):
    if request.body == b"":
        return [0, JsonResponse({
            "errormsg" : "no body" , 
            "errorflag": True,
        })]

    password = str( JSONDecode(request.body).get("password") )
    if len(Password.objects.filter(val = password)) == 0: # not a valid password
        return [0, JsonResponse({
            "errormsg" : "not a valid password" , 
            "errorflag": True,
        })]

    return [1, None]

def get_variable(request , key):

    flag, info = check_password(request)
    if flag == 0:
        return info

    var = Variable.objects.filter(key = key)
    if len(var) == 0: # no such variable
        return JsonResponse({
            "errormsg" : "can't find variable" , 
            "errorflag": True,
        })
    var = var [0]

    return JsonResponse({
        "val": var.val , 
        "errorflag": False , 
    })

def get_function(request , key):

    flag, info = check_password(request)
    if flag == 0:
        return info

    fun = Function.objects.filter(key = key)
    if len(fun) == 0: # no such function
        return JsonResponse({
            "errormsg" : "can't find function" , 
            "errorflag": True,
        })
    fun = fun [0]

    val_raw = str(fun.val)
    val = val_raw
    for var in Variable.objects.all(): # 也许有不这么粗暴的方法...
        val = val.replace( "${" + str(var.key) + "}" , str(var.val))


    return JsonResponse({
        "val": val , 
        "val_raw": val_raw , 
        "errorflag": False , 
    })

def get_rand_str():
	s = ""
	for i in range(10):
		s += "qwertyuioplkjhgfdsazxcvbnm"[random.randint(0,26-1)]
	return s

def add_password(request , key):

    flag, info = check_password(request)
    if flag == 0:
        return info

    new_pass = Password(key = key, val = get_rand_str())
    new_pass.save()


    return JsonResponse({
        "val": new_pass.val , 
        "errorflag": False , 
    })

def delete_password(request , key):

    flag, info = check_password(request)
    if flag == 0:
        return info

    Password.objects.filter(key = key).delete()

    return JsonResponse({
        "errorflag": False , 
    })
