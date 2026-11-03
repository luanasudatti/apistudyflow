# StudyFlow API (v1)

## Rodar

    python -m venv venv
    source venv/bin/activate          # Windows: venv\Scripts\activate
    pip install -r requirements.txt
    export JWT_SECRET_KEY="uma-chave-longa-e-aleatoria"   # Windows: set JWT_SECRET_KEY=...
    python app.py

Servidor em http://127.0.0.1:5000 (emulador Android: http://10.0.2.2:5000).

## Testar com curl

    # cadastro e login
    curl -X POST localhost:5000/auth/cadastro -H "Content-Type: application/json" \
      -d '{"nome":"Ana","email":"ana@example.com","senha":"senha_segura"}'
    curl -X POST localhost:5000/auth/login -H "Content-Type: application/json" \
      -d '{"email":"ana@example.com","senha":"senha_segura"}'

    # use o token retornado
    TOKEN="cole_o_token_aqui"
    curl -X POST localhost:5000/materias -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"nome":"Matemática","professor":"Prof. X","limite_faltas":10}'
    curl -X POST localhost:5000/horario -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"materia_id":1,"dia_semana":0,"inicio":"08:00","fim":"08:50","periodo":1}'
    curl -X POST localhost:5000/frequencia -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"horario_id":1,"data":"2026-10-28","status":"absent"}'
    curl localhost:5000/frequencia/resumo -H "Authorization: Bearer $TOKEN"
    curl localhost:5000/frequencia/pode-faltar -H "Authorization: Bearer $TOKEN"

## Decisões
- Prazo da tarefa é uma data (AAAA-MM-DD); "vencida" = prazo antes de hoje e não concluída.
- Excluir matéria apaga horários e frequência dela.
- Banco criado com db.create_all(); se mudar os modelos, apague studyflow.db (ou adote Flask-Migrate).
