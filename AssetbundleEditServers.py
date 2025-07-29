import UnityPy
import json
import os

input_path = "../rcc_Data/resources.assets"
output_path = "../rcc_Data/resources_patched.assets"

new_config = [
    {
        "Name": "dev",
        "WebSocketUrl": "ws://127.0.0.1/websocket",
        "Host": "127.0.0.1",
        "Port": 27130,
        "PolicyPort": 27132,
        "ServerURL": "http://127.0.0.1/mochiweb/",
        "ImageURL": "http://127.0.0.1/rcc/",
        "ProxyURL": "http://127.0.0.1/",
        "FacebookOGUrl": "http://127.0.0.1/rcc/open_graph/",
        "FacebookApp": "ridingclub_dev"
    },
    {
        "Name": "live",
        "WebSocketUrl": "ws://127.0.0.1/websocket",
        "Host": "127.0.0.1",
        "Port": 27130,
        "PolicyPort": 27132,
        "ServerURL": "http://127.0.0.1/mochiweb/",
        "ImageURL": "http://127.0.0.1/rcc/",
        "ProxyURL": "http://127.0.0.1/",
        "FacebookOGUrl": "http://127.0.0.1/rcc/open_graph/",
        "FacebookApp": "ridingclub"
    },
    {
        "Name": "local",
        "WebSocketUrl": "ws://127.0.0.1/websocket",
        "Host": "127.0.0.1",
        "Port": 27130,
        "PolicyPort": 27132,
        "ServerURL": "http://127.0.0.1/mochiweb/",
        "ImageURL": "http://127.0.0.1/rcc/",
        "ProxyURL": "http://127.0.0.1/",
        "FacebookOGUrl": "http://127.0.0.1/rcc/open_graph/",
        "FacebookApp": "ridingclub_dev"
    },
    {
        "Name": "igor",
        "WebSocketUrl": "ws://127.0.0.1/websocket",
        "Host": "127.0.0.1",
        "Port": 27130,
        "PolicyPort": 27132,
        "ServerURL": "http://127.0.0.1/mochiweb/",
        "ImageURL": "http://127.0.0.1/rcc/",
        "ProxyURL": "http://127.0.0.1/",
        "FacebookOGUrl": "http://127.0.0.1/rcc/open_graph/",
        "FacebookApp": "ridingclub_dev"
    }
]

json_text = json.dumps(new_config, indent=4)
escaped_json = json_text.replace('\n', '\r\n')  # No need to escape quotes here

env = UnityPy.load(input_path)

patched = False

for obj in env.objects:
    if obj.type.name == "TextAsset":
        data = obj.read()
        if data.m_Name == "servers":
            print(f"✅ Found TextAsset: {data.m_Name}")
            data.m_Script = escaped_json
            data.save()
            patched = True

if not patched:
    print("❌ Could not find TextAsset named 'servers'.")
else:
    with open(output_path, "wb") as f:
        f.write(env.file.save(packer="UnityFS"))
    print(f"✅ Patched asset saved to: {output_path}")
