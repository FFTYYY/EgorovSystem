from pathlib import Path
import os

template = """server {
    listen 80;  
    server_name %s; 

    location / {
        proxy_pass %s;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
"""

def run():
    server_name = input("Please input the server name: ").strip()
    url         = input("Please input the target url: ").strip()
    nginx       = input("Please input nginx location (enter for default): ").strip()
    my_name     = "egorovsystem"

    content = template % (server_name, url)

    nginx = Path("/etc/nginx") if nginx == "" else Path(nginx)

    with open(nginx / "sites-available" / my_name, "w") as f:
        f.write(content)
    
    os.system(f"rm {nginx / 'sites-enabled' / my_name}")
    os.system(f"ln -s {nginx / 'sites-available' / my_name} {nginx / 'sites-enabled' / my_name}")
    os.system("sudo systemctl restart nginx")


if __name__ == "__main__":
    run()