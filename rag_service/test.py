import chromadb

client = chromadb.HttpClient(host='chromadb', port=8000)
print(client.heartbeat())