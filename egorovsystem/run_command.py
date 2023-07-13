from . import get_function
import sys
import os
import tempfile
from YTools import python_run

def run():
    argv = sys.argv
    
    if len(argv) == 1:
        return 
    
    function = get_function(argv[1])

    if function["type"] == "cmd":
        os.system(function["content"])
    if function["type"] == "python":
        python_run(function["content"])

if __name__ == "__main__":
    sys.argv = ["", "test"]
    run()