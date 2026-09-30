"""
Módulo: scheduler.py
Executa a busca diariamente às 09:00 e às 15:00 (Fuso de Brasília / Tocantins).
"""

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from buscador_pncp import executar_varredura_nacional

if __name__ == "__main__":
    scheduler = BlockingScheduler(timezone="America/Sao_Paulo")
    
    # Agendamento para 09h e 15h todos os dias
    trigger = CronTrigger(hour="9,15", minute="0")
    scheduler.add_job(executar_varredura_nacional, trigger=trigger, name="Varredura_PNCP_09h_15h")
    
    print("Agendador iniciado. Disparos programados para 09:00 e 15:00.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Agendador encerrado.")
