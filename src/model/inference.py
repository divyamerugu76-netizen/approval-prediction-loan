import joblib
import pandas as pd

model = joblib.load("artifacts/model.pkl")

def predict(input_data):
    df = pd.DataFrame([input_data]) 
    result = model.predict(df)
    return result[0]