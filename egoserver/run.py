import os
import sys
import argparse

def init():
	os.chdir(os.path.dirname(__file__))
	sys.path.append(os.path.abspath("."))

	if os.system("python -V") != 0:
		os.system("python3 manage.py migrate")
		os.system("python3 manage.py createsuperuser")
	else:
		os.system("python manage.py migrate")
		os.system("python manage.py createsuperuser")


def run():
	os.chdir(os.path.dirname(__file__))
	sys.path.append(os.path.abspath("."))

	C = argparse.ArgumentParser()
	C.add_argument("--port" , type = int , default = 7999)
	C = C.parse_args()

	if os.system("python -V") != 0:
		os.system("python3 manage.py migrate")
		os.system("python3 manage.py runserver 0.0.0.0:%d" % (C.port))
	else:
		os.system("python manage.py migrate")
		os.system("python manage.py runserver 0.0.0.0:%d" % (C.port))
