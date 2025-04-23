import yfinance as yf

def get_stock_price(stock_symbol):
    stock = yf.Ticker(stock_symbol)
    return stock.history(period="1d")["Close"][0]