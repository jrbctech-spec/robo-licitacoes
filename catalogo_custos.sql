create table catalogo_custos (
    id serial primary key,
    codigo_referencia text unique, -- ex: 'SSD-480GB-WD', 'MENSAL-SUPORTE-N1'
    tipo text check (tipo in ('PRODUTO', 'SERVICO_MENSAL')),
    custo_base decimal(10,2) not null,
    imposto_aliquota decimal(5,4) default 0.0000, -- MEI = 0% de imposto por item (paga DAS fixo)
    frete_ou_despesa decimal(10,2) default 0.00,
    distribuidor_origem text
);
