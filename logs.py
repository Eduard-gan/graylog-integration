import aiologger
import structlog
from aio_pika import Message, ExchangeType
from aio_pika import connect_robust
from aiologger.handlers.streams import AsyncStreamHandler
from aiologger.levels import LogLevel
from graystruct.encoder import GELFEncoder

BROKER_CONNECTION = None
BROKER_CHANNEL = None
EXCHANGE = None
DEFAULT_LOGGING_LEVEL = LogLevel.WARNING


class SyncConsoleHandler(AsyncStreamHandler):
    """
    Класс создан потому что оригинальный логгинг в консоль через asynclog почему-то падает.
    """
    async def emit(self, record):
        print(record.msg)


class RabbitMQHandler(AsyncStreamHandler):
    broker_user = "mfo"
    broker_user_password = "01014949"
    broker_ip_address = "172.17.0.1"
    exchange_name = "logging.gelf"
    exchange_type = ExchangeType.FANOUT
    queue_name = "gelf_log"

    @classmethod
    async def connect_to_broker(cls):
        """
        Общая точка для получения канала для работы с брокером.
        Должна быть использована любыми пользователями броекра.
        Возвращает канал к брокеру RabbitMQ, используя по возможности уже существующие в переменных BROKER_CONNECTION и BROKER_CHANNEL.
        Если они отсутствуют, то функция создает их и помещает в эти переменные.
        """
        from time import sleep

        global BROKER_CONNECTION
        global BROKER_CHANNEL

        retries = 0
        while not BROKER_CONNECTION:
            print("Trying to create connection to broker")
            try:
                BROKER_CONNECTION = await connect_robust(f"amqp://{cls.broker_user}:{cls.broker_user_password}@{cls.broker_ip_address}/")
                print(f"Connected to broker ({type(BROKER_CONNECTION)} ID {id(BROKER_CONNECTION)})")
            except Exception as e:
                retries += 1
                print(f"Can't connect to broker {retries} time({e.__class__.__name__}:{e}). Will retry in 5 seconds...")
                sleep(5)

        if not BROKER_CHANNEL:
            print("Trying to create channel to broker")
            BROKER_CHANNEL = await BROKER_CONNECTION.channel()
            print("Got a channel to broker")
        return BROKER_CHANNEL

    async def emit(self, record):
        """
        Асинхронная отравка сообщения в брокер, для Graylog.
        """
        global EXCHANGE

        if not BROKER_CHANNEL:
            await self.connect_to_broker()

        if not EXCHANGE:
            EXCHANGE = await BROKER_CHANNEL.declare_exchange(self.exchange_name, self.exchange_type, durable=True)
        message = Message(body=record.msg.encode())
        await EXCHANGE.publish(message, self.queue_name)


class AsyncWrapper(structlog.BoundLogger):
    def _proxy_to_logger(self, method_name, event=None, **event_kw):
        """
        Переопределен потому что оригинальная обертка иногда возвращает non-awaitable None
        """
        return super()._proxy_to_logger(method_name, event, **event_kw) or self.async_pass()

    @staticmethod
    async def async_pass():
        return None


class AsyncLogger(aiologger.Logger):
    def isEnabledFor(self, level):
        """
        Для совместимости со Structlog
        :param level: Уровень приоритета сообщения
        :return: Возвращает булеан который определяет будет ли логгироваться этим логгером данное сообщение.
        """
        return True if level >= self.level.value else False

    @classmethod
    def factory(cls, name, level, **kwargs):
        """
        Фабрика настраивет экземпляр логгера добавляя к нему два хэндлера, один для логгирования в консоль, а другой
        для асинхронной отправки сообщений с логами в RabbitMQ откуда их забирает сервер Graylog.
        :param name: Имя назначаемое логгеру
        :param level: Минимальный уровень приоритета сообщений, которые будут логгироваться логером
        :param kwargs: Контекстные переменные
        """
        self = cls(name=name, level=level, **kwargs)
        self.add_handler(SyncConsoleHandler(level=level))
        self.add_handler(RabbitMQHandler(level=level))
        return self

    @staticmethod
    def add_app_context(_, __, event_dict):
        from structlog._frames import _find_first_app_frame_and_name
        f, name = _find_first_app_frame_and_name([__name__])
        event_dict['file'] = f.f_code.co_filename
        event_dict['line'] = f.f_lineno
        event_dict['function'] = f.f_code.co_name
        return event_dict

    @classmethod
    def get_logger(cls, name="NO_NAME", level=DEFAULT_LOGGING_LEVEL):
        """
        Все настройки логгирования через Structlog.
        :param name: Имя назначаемое логгеру
        :param level: Минимальный уровень приоритета сообщений, которые будут логгироваться логером
        :return: Сконфигурированный и обернутый в wrapper_class объект-логгер.
        """
        structlog.configure(
            logger_factory=cls.factory,
            wrapper_class=AsyncWrapper,
            processors=[
                # Prevent exception formatting if logging is not configured
                structlog.stdlib.filter_by_level,
                # Add file, line, function information of where log occurred
                cls.add_app_context,
                # Format positional args to log as in stdlib
                structlog.stdlib.PositionalArgumentsFormatter(),
                # Add a timestamp to log message
                structlog.processors.TimeStamper(fmt='iso', utc=True),
                # Dump stack if ``stack_info=True`` passed to log
                structlog.processors.StackInfoRenderer(),
                # Format exception info is ``exc_info`` passed to log
                structlog.processors.format_exc_info,
                # Encode the message in GELF format (this must be the final processor)
                GELFEncoder(),
            ],
        )
        return structlog.get_logger(name, level)
