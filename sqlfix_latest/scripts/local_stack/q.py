import sys, json
from e2e_lib import *
print(json.dumps(db("GET", sys.argv[1]), default=str)[:int(sys.argv[2]) if len(sys.argv) > 2 else 3000])
