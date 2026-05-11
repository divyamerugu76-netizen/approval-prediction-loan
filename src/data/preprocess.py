import pandas as pd

def load_data(path):
    data = pd.read_csv(path)
    data.columns = data.columns.str.strip()
    return data


def preprocess_data(data):
    data['loan_status'] = data['loan_status'].fillna(
        data['loan_status'].mode()[0]
    )

    x = data[['education', 'self_employed',
              'income_annum', 'loan_amount', 'loan_term', 'cibil_score',
            ]]

    y = data['loan_status']

    return x, y