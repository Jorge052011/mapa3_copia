Algunos manejos del código para recordar



Crean copas en base de datos para copiar y pegar en consola

Comando directo para copiar una base de datos sqlite3.
bash# Backup con fecha
cp db.sqlite3 backup_$(date +%Y%m%d_%H%M%S).db
Mejor opción: con carpeta
bash# Crear carpeta y hacer backup
mkdir -p backups
cp db.sqlite3 backups/backup_$(date +%Y%m%d_%H%M%S).db

source venv/bin/activate

Para ajustar codigo a pantalla coloca crt+shif+p y selecciona toggel word wrap, se ajustará el codigo a la pantalla.

comando para copiar los programas que use pip freeze > requirements.txt


para copiar del servidor a girhub

cd ~/proyectos/mapa3_copia
cp db.sqlite3 backups/backup_$(date +%Y%m%d_%H%M%S).db
git add backups/
git commit -m "Respaldo $(date +%Y%m%d)"
git push origin main