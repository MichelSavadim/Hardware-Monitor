# Hardware Monitor

Widget pessoal para Windows 10. Mostra dez métricas em gráficos de 90 amostras, com atualização de um segundo. Cada métrica pode ser desligada pelo ícone na bandeja; a janela se ajusta automaticamente.

## Executar

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\iniciar-widget.bat
```

Clique na engrenagem do cabeçalho para ativar ou desativar cada métrica. O alfinete fixa a posição e impede arrastar; clique novamente para mover. O menu da bandeja também oferece essas opções e o modo de sobrepor todas as janelas. As escolhas e a posição são salvas em `settings.json`.

`iniciar-widget.bat` valida o ambiente, solicita acesso de administrador e reinicia apenas instâncias anteriores deste widget. A coleta básica de CPU, RAM e GPU aparece independentemente do carregamento dos sensores do LibreHardwareMonitor, que pode levar alguns segundos. Se alguma leitura continuar vazia, confira `logs/launcher.log`, `logs/widget.log` e `logs/widget-errors.log`.

No modo normal, o widget fica preso à camada dos ícones da área de trabalho do Explorer e permanece visível após **Win+D**. A opção **Sobrepor janelas** mantém o widget acima dos demais aplicativos.

## Sensores

- GPU: `nvidia-smi`, incluído no driver NVIDIA. Potência e gráfico da GPU são exibidos em watts.
- Uso de CPU e RAM: `psutil`.
- Temperatura e potência da CPU e clock **dinâmico** da RAM: biblioteca oficial do [LibreHardwareMonitor 0.9.6](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/tag/v0.9.6), incluída em `vendor/LibreHardwareMonitor`. Use `iniciar-widget.bat`; o Windows solicitará permissão de administrador para acessar esses sensores. O driver PawnIO do LibreHardwareMonitor precisa estar instalado. Como alternativa, o widget aceita o servidor web do LibreHardwareMonitor em `http://127.0.0.1:8085/data.json`.

O clock da RAM exibido é o clock físico do sensor. Em DDR, a taxa efetiva de transferências costuma ser o dobro; por exemplo, cerca de 1333 MHz corresponde a 2667 MT/s. Sensores ainda não disponíveis aparecem como `—`.

Neste Ryzen 5 5600, a tabela SMU tem versão `0x00380905`. Como essa versão não é mapeada pelo LibreHardwareMonitor 0.9.6, o widget lê o clock da RAM diretamente da posição 51 do relatório dinâmico dessa tabela. Essa leitura específica foi validada neste PC.

## Resumo de energia para consulta por IA

O widget regrava `consumo_atual.json` a cada segundo. Esse arquivo pequeno contém os totais da sessão atual, o tempo de atividade e os segundos com leituras válidas. Uma nova execução começa uma nova contagem da sessão.

Para consultar dias anteriores, leia os arquivos `logs/diario/AAAA-MM-DD.json`. Há um resumo compacto por dia, preservado indefinidamente. O resumo do dia atual é atualizado a cada segundo e soma as sessões do widget naquele dia. Cada arquivo contém a energia acumulada da CPU, da GPU e a soma em **Wh** e **kWh**, além do tempo de atividade do monitor naquele dia. Os arquivos são substituídos de forma atômica, para que outro programa leia sempre um JSON completo. Nenhuma amostra individual é guardada nesses resumos.

A energia é calculada a partir da potência média entre leituras consecutivas e do tempo real decorrido. O primeiro intervalo só é estimado se a primeira leitura estiver disponível. Intervalos sem leitura não são somados à energia acumulada. Desativar um gráfico não interrompe o cálculo. O total inclui apenas CPU e GPU, não o consumo completo do computador na tomada. **Wh** e **kWh** são unidades de energia; W/h e kW/h indicariam variação de potência por hora.
