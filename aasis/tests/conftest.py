"""Compatibilidade de testes com Windows: ele não apaga um arquivo de banco ainda aberto ao limpar a pasta temporária.
O app fecha tudo ao sair; nos testes a conexão vive até o fim do bloco `with`, então ignoramos esse erro de limpeza."""
import tempfile

_Original = tempfile.TemporaryDirectory


class _TemporaryDirectory(_Original):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("ignore_cleanup_errors", True)
        super().__init__(*args, **kwargs)


tempfile.TemporaryDirectory = _TemporaryDirectory
