import json

def JSONDecode(s):
    s = s.strip()
    if s == "":
        return {}
    
    ret = {}
    try:
        ret = json.loads(s)
    except json.JSONDecodeError:
        pass

    return ret
