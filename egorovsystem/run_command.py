from . import get_function
import sys
import os

def run():
    argv = sys.argv
    
    if len(argv) == 1:
        return 
    
    function = get_function(argv[1])
    os.system(function)

if __name__ == "__main__":
    sys.argv = ["", "test"]
    run()