# WATransfer

Backup e leitura do **WhatsApp Business** de celulares Android e iPhone, no Windows.
Esta é a etapa 1 (backup + visualização/exportação). A conversão Android ↔ iPhone
e a restauração no aparelho de destino são as próximas etapas.

## Instalar em outro computador (Windows 10/11)

1. Instale o **Python 3.11 ou superior** (python.org), marcando **"Add python.exe to PATH"**.
2. Baixe o projeto: `git clone https://github.com/Wagnerpila/watransfer.git`
   (ou, no GitHub, **Code → Download ZIP** e extraia).
3. Para **iPhone**: instale o app **Apple Devices** (Microsoft Store) ou o iTunes.
4. Para **Android**: instale o [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools)
   (contém o `adb`) e coloque a pasta no PATH, ou defina `WATRANSFER_ADB` com o caminho do `adb.exe`.

## Como abrir

Dê dois cliques em `iniciar.bat`. Na primeira vez ele cria o ambiente Python e instala as dependências
(precisa de internet; leva alguns minutos). Se a instalação for interrompida, basta rodar de novo.

Os backups ficam em `Documentos\WATransfer\backups` (ou na pasta definida em `WATRANSFER_HOME`).

## Android

1. No WhatsApp Business: **Configurações → Conversas → Backup de conversas →
   Backup criptografado de ponta a ponta**. Ative e escolha **usar chave de criptografia de 64 dígitos**. Guarde a chave.
2. Toque em **Fazer backup**.
3. Ative a **Depuração USB** (Opções do desenvolvedor), conecte o cabo e autorize o computador.
4. Na aba **Android**, escolha o aparelho, cole a chave e clique em **Fazer backup do Android**.

O sistema copia `msgstore.db.crypt15` (e a pasta `Media`) e o descriptografa localmente.
Nenhum dado é enviado a servidores. A chave não é salva em disco.

Precisa do `adb`. O sistema procura no PATH, no Android SDK e na pasta do iCareFone;
para usar outro, defina `WATRANSFER_ADB`.

## iPhone

1. Instale o **Apple Devices** (ou iTunes) para ter o driver USB do iPhone.
2. Conecte, desbloqueie e toque em **Confiar**.
3. Na aba **iPhone**, use um backup existente ou crie um novo. Backups criptografados pedem a senha do backup.

## Linha de comando

```
.venv\Scripts\python -m watransfer android-devices
.venv\Scripts\python -m watransfer android-backup <serial>
.venv\Scripts\python -m watransfer android-import msgstore.db.crypt15 --media Media
.venv\Scripts\python -m watransfer ios-backups
.venv\Scripts\python -m watransfer ios-extract "<pasta do backup>" --password ...
.venv\Scripts\python -m watransfer list
.venv\Scripts\python -m watransfer export "<pasta do backup salvo>" -o saida --format html
```

## Testes

```
.venv\Scripts\python -m pytest -q
```

## Limitações conhecidas

- Android: só backups `.crypt15` (backup com chave de 64 dígitos). `.crypt14`/`.crypt12` exigem
  o arquivo de chave interno do aparelho (root).
- Os nomes de contatos do Android ficam no `wa.db`, que não faz parte do backup; as conversas
  individuais aparecem pelo número.
- Os formatos dos bancos do WhatsApp mudam entre versões; os leitores toleram colunas ausentes,
  mas foram testados com bancos sintéticos e precisam ser validados com backups reais.
