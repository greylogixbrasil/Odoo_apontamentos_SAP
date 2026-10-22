import threading


class AppState:
    def __init__(self):
        self.lock = threading.Lock()
        # namespace independente do state.py da app/ (processo separado) - progresso de um
        # "Rodar agora" disparado a partir desta app
        self.automacao = {
            "em_andamento": False,
            "pos": 0,
            "total": 0,
            "logs": [],
            "execucao_id": None,
            "resumo": None,
            "erro": None,
        }
        # envio do tempo nao alocado (app_acompanhamento/api/nao_alocado.py)
        self.nao_alocado = {
            "em_andamento": False,
            "pos": 0,
            "total": 0,
            "logs": [],
            "execucao_id": None,
            "resumo": None,
            "erro": None,
        }


state = AppState()
