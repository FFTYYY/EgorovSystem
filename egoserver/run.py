import os
import sys
import argparse
from YTools import python_run_file

def init():
	os.chdir(os.path.dirname(__file__))
	sys.path.append(os.path.abspath("."))
	
	python_run_file("manage.py migrate")
	python_run_file("manage.py createsuperuser")


def run():
	os.chdir(os.path.dirname(__file__))
	sys.path.append(os.path.abspath("."))

	C = argparse.ArgumentParser()
	C.add_argument("--port" , type = int , default = 7999)
	C = C.parse_args()

	python_run_file("manage.py migrate")
	python_run_file("manage.py runserver 0.0.0.0:%d" % (C.port))
