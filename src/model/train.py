from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
from src.data.preprocess import load_data, preprocess_data
from src.pipeline.bulid_pipeline import build_pipeline
import joblib
import os

data=load_data('C:/Users/Divya/Loan Approval Prediction/data/loan_approval_dataset.csv')
x,y=preprocess_data(data)

x_train,x_test,y_train,y_test=train_test_split(
    x,y,test_size=0.2,random_state=42)


model=build_pipeline(x_train)

model.fit(x_train,y_train)

test=model.predict(x_test)
train=model.predict(x_train)

print(accuracy_score(y_test,test))
print(accuracy_score(y_train,train))

os.makedirs("artifacts", exist_ok=True)
joblib.dump(model,"artifacts/model.pkl")
    