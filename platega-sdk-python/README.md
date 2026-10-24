# Platega Python SDK

Python SDK для интеграции с платёжной системой [Platega.io](https://platega.io).

Документация API: [https://docs.platega.io/llms.txt](https://docs.platega.io/llms.txt)

## Установка

```bash
pip install platega
```

Или установка из исходников:

```bash
pip install -e .
```

## Требования

- Python 3.7+
- Без внешних зависимостей (только стандартная библиотека)

## Быстрый старт

### Создание платежа

```python
from platega import Platega

# Инициализация клиента
client = Platega('your-merchant-id', 'your-secret-key')

# Создание платежа
result = client.create_payment(
    amount=1000,
    currency='RUB',
    payment_method=Platega.METHOD_SBP_QR,
    description='Оплата заказа #123',
    return_url='https://yoursite.com/success',
    failed_url='https://yoursite.com/fail',
    payload='123'  # ID заказа
)

# Перенаправление на страницу оплаты
print(result['redirect'])
print(result['transactionId'])
```

### Проверка статуса платежа

```python
from platega import Platega

client = Platega('your-merchant-id', 'your-secret-key')

status = client.get_payment_status('transaction-uuid')
print(f"Статус: {status['status']}")
print(f"Сумма: {status['paymentDetails']['amount']}")
```

### Обработка callback (Flask)

```python
from flask import Flask, request
from platega import PlategaCallback

app = Flask(__name__)

@app.route('/callback', methods=['POST'])
def payment_callback():
    callback = PlategaCallback('your-merchant-id', 'your-secret-key')
    
    if not callback.validate(request):
        return callback.get_validation_error(), 401
    
    order_id = callback.get_order_id()
    amount = callback.get_amount()
    
    if callback.is_success():
        # Платёж успешен
        update_order_status(order_id, 'paid')
        
    elif callback.is_canceled():
        # Платёж отменён
        update_order_status(order_id, 'canceled')
        
    elif callback.is_chargeback():
        # Chargeback
        update_order_status(order_id, 'chargeback')
    
    return 'OK', 200
```

### Обработка callback (Django)

```python
from django.http import HttpResponse
from platega import PlategaCallback

def payment_callback(request):
    callback = PlategaCallback('your-merchant-id', 'your-secret-key')
    
    if not callback.validate_django(request):
        return HttpResponse(callback.get_validation_error(), status=401)
    
    if callback.is_success():
        order_id = callback.get_order_id()
        # Обработка успешного платежа
    
    return HttpResponse('OK')
```

## Способы оплаты

| Константа | Значение | Описание |
|-----------|----------|----------|
| `Platega.METHOD_SBP_QR` | 2 | СБП (QR) + Sberpay |
| `Platega.METHOD_ERIP` | 3 | ЕРИП |
| `Platega.METHOD_CARD_ACQUIRING` | 11 | Карточный эквайринг |
| `Platega.METHOD_INTERNATIONAL` | 12 | Международная оплата |
| `Platega.METHOD_CRYPTO` | 13 | Криптовалюта |
| `Platega.METHOD_SUBSCRIPTION` | 6 | СБП-подписка (`create_subscription`) |
| `Platega.METHOD_CARDS_RUB` | 10 | Legacy |

Без `payment_method` создаётся платёж через `/v2/transaction/process` (выбор метода на стороне Platega).

Также доступны: `get_balances`, `is_cancel_supported`, `cancel_transaction`, `get_conversions`, `create_subscription` / `get_subscription` / `list_subscriptions` / `cancel_subscription`, `get_cards` (HMAC).

```python
methods = Platega.get_payment_methods()
```

## Статусы платежей

| Константа | Значение | Описание |
|-----------|----------|----------|
| `Platega.STATUS_PENDING` | PENDING | Ожидает оплаты |
| `Platega.STATUS_CONFIRMED` | CONFIRMED | Успешно оплачен |
| `Platega.STATUS_CANCELED` | CANCELED | Отменён |
| `Platega.STATUS_CHARGEBACKED` | CHARGEBACKED | Chargeback |

## Обработка ошибок

```python
from platega import Platega, PlategaAPIError

client = Platega('merchant_id', 'secret')

try:
    result = client.create_payment(1000, 'RUB', Platega.METHOD_SBP_QR)
except PlategaAPIError as e:
    print(f"Ошибка: {e}")
    print(f"HTTP код: {e.http_code}")
    print(f"Данные: {e.response_data}")
```

## Callback URL

Укажите этот URL в настройках личного кабинета Platega:

- **Flask**: `https://yoursite.com/callback`
- **Django**: `https://yoursite.com/payment/callback/`

## Лицензия

MIT License

## Изменения SDK 1.2.0 (7 октября 2026)

Контракт проверен по https://docs.platega.io/llms.txt. Вариант без метода использует
`/v2/transaction/process`; `null`/`None` и `0` выбирают универсальную форму.
Метод 10 преобразуется в 11. Добавлено поле `orderId`.
В запросе создания передавайте `description`, адрес успешного возврата и
`failedUrl`/`failed_url`: документация API отмечает их обязательными. SDK сохраняет
старые сигнатуры и не придумывает адреса за приложение. `id` транзакции не передаётся.

Сумма должна быть конечным положительным числом. SDK не пересчитывает валюты и
не зачисляет деньги: приложение обязано связать транзакцию с заказом, проверить
сумму И валюту, сохранить исходный курс и обеспечить однократное зачисление.
После неуспешной валидации callback данные и признак успешной оплаты сбрасываются.
Поддерживаются обычные callback и поля с заглавной первой буквой из callback подписок.
Событие `SUBSCRIPTION_ACTIVATED` не считается оплаченной транзакцией.
Лимит тела callback — 64 KiB. В HTTP-сервере и body-parser также установите лимит:
уже прочитанное middleware тело SDK не может ограничить задним числом.

Подписки требуют целую сумму RUB и `intervalCount`: день 1..31, неделя 1..4,
месяц 1..12, год 1..3; по умолчанию 1. Методы статуса, QR, балансов, возвратов,
списка/отмены подписок, карт и экспортов сохранены. Старые `getRate`/`get_rate`
и `getConversions`/`get_conversions` оставлены для совместимости, но отсутствуют
в текущем индексе документации: их доступность сервером не подтверждена.

Payout API использует отдельный ключ, выдаваемый в разделе Payout API, а не
обычный API Secret. Для карт и выводов укажите этот ключ при создании клиента.
`createCardPayout`/`create_card_payout` требует сохранённый `Idempotency-Key`,
ровно одно поле `cardId` или `cardNumber`, целую сумму 1000..87500 RUB.
Подписываются те же JSON-байты, которые уходят в запрос. Автоматических повторов
создания платежей, возвратов и выводов нет. При неизвестном результате сохраняйте
первоначальную операцию и ключ, не создавайте замену с новым ключом.

API-редиректы не выполняются, TLS проверяется. Для некоторых магазинов необходимо
передавать `metadata.userId`; необходимость согласуется с Platega. Дополнительные
поля metadata SDK передаёт без привязки к конкретной CMS.

```python
client = Platega(merchant_id, api_secret, payout_secret=payout_secret)
subscription = client.create_subscription(500, 'RUB', 3, 'Подписка', interval_count=1)
# Payout API secret отличается от обычного API secret.
cards = client.get_cards(False)
# client.create_card_payout({'cardId': saved_card_id, 'amountRub': 1500}, saved_idempotency_key)
```
Проверка без сети: `python tests/test_sdk.py`. Python 3.7.17 и 3.14.3 проверены выполнением тестов.
