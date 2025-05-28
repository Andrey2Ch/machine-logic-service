-- Создание только таблицы cards
CREATE TABLE IF NOT EXISTS cards (
    card_number INTEGER NOT NULL,
    machine_id BIGINT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'free',
    batch_id BIGINT NULL,
    last_event TIMESTAMP NOT NULL DEFAULT NOW(),
    
    PRIMARY KEY (card_number, machine_id),
    
    FOREIGN KEY (machine_id) REFERENCES machines(id),
    FOREIGN KEY (batch_id) REFERENCES batches(id),
    
    CHECK (status IN ('free', 'in_use', 'lost')),
    CHECK (card_number >= 1 AND card_number <= 20)
); 