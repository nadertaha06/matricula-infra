# Incluido em todo bloco que repassa requisicoes. No Nginx, um location que define
# qualquer proxy_set_header deixa de herdar os do server; por isso a lista fica aqui.
proxy_set_header X-Service-Token "";
proxy_set_header Host $http_host;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto https;
proxy_set_header X-Request-ID $request_id;
