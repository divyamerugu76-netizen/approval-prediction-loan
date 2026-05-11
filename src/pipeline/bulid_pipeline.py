from sklearn.preprocessing import StandardScaler,OneHotEncoder
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.ensemble import RandomForestClassifier

def build_pipeline(x):
    numeric_features=x.select_dtypes(include=['int64','float64']).columns
    categorical_features=x.select_dtypes(include=['object']).columns

    numeric_pipeline=Pipeline(steps=[
    ('imputer',SimpleImputer(strategy='median')),
    ('scaler',StandardScaler())
    ])

    categorical_pipeline=Pipeline(steps=[
    ('imputer',SimpleImputer(strategy='most_frequent')),
    ('onehot',OneHotEncoder(handle_unknown='ignore'))
    ])

    preprpcessor=ColumnTransformer([
    ('numeric',numeric_pipeline,numeric_features),
    ('categorical',categorical_pipeline,categorical_features)
    ])

    model_pipeline=Pipeline(steps=[
    ('preprocessor',preprpcessor),
    ('model',RandomForestClassifier())
    ])

    return model_pipeline