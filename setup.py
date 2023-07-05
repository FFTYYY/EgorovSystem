from setuptools import setup, find_packages

with open("README.md", encoding="utf-8") as f:
	readme = f.read()

with open("LICENSE", encoding="utf-8") as f:
	license = f.read()

with open("requirements.txt", encoding="utf-8") as f:
	reqs = f.read()

setup(
	name					= "egorovsystem",
	version					= "0.0.1",
	description				= "",
	long_description		= readme,
	long_description_content_type	= "text/markdown",
	license					= license,
	author					= "Yongyi Yang",
	python_requires			= ">=3.6",
	packages				= ["egoserver", "egolocal", "egorovsystem"],
	install_requires		= reqs.strip().split("\n"),
	entry_points			= {"console_scripts": [
		"egoserver-init=egoserver.run:init" , 
		"egoserver-run=egoserver.run:run" , 
		"egolocal-run=egolocal.main:run" , 
	]}
)
