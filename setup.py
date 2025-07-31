from setuptools import setup, find_packages

with open("README.md", encoding="utf-8") as f:
	readme = f.read()

with open("LICENSE", encoding="utf-8") as f:
	license = f.read()

# pip 在搞什么飞机
try:
	with open("requirements.txt", encoding="utf-8") as f:
		reqs = f.read()
except:
	reqs = ""

print (find_packages())
pkgs = [p for p in find_packages() if p.startswith("ego")]
print(pkgs)

setup(
	name					= "egorovsystem",
	version					= "0.0.9.post13",
	description				= "",
	long_description		= readme,
	long_description_content_type	= "text/markdown",
	license					= license,
	author					= "Yongyi Yang",
	author_email 			= "yongyi@umich.edu",
	python_requires			= ">=3.6",
	packages				= pkgs,
	install_requires		= reqs.strip().split("\n"),
	entry_points			= {"console_scripts": [
		"egoserver-init=egoserver.run:init" , 
		"egoserver-run=egoserver.run:run" , 
		"egolocal-run=egolocal.main:run" , 
		"EGO=egorovsystem.run_command:run" , 
		"egoserver-setnginx=egoserver_setnginx.main:run" , 
	]} ,
)
