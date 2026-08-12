from prdforge.a2a_common import build_app, serve
from prdforge.agents.requirements.agent import PORT, card, executor

app = build_app(card(), executor())

if __name__ == "__main__":
    serve(app, PORT)
