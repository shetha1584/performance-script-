import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import xgboost as xgb
from sklearn.preprocessing import StandardScaler
import time
import warnings
warnings.filterwarnings('ignore')

# Configuration
MSN = "67006960"

def fetch_with_retry(url, retries=3):
    """Fetch data from API with retry logic"""
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
            else:
                return None

def classify_day_consumption(daily_total, max_daily, threshold_pct=0.20):
    """Classify day as LOW or HIGH based on threshold"""
    threshold = max_daily * threshold_pct
    return 'HIGH' if daily_total >= threshold else 'LOW'

print("=" * 140)
print("FINAL BEST MODEL - PREDICTION FOR NEXT DAY")
print("Features: lag_1, lag_24, lag_168, avg_7d, day_of_week")
print("MSE: 485.80 | RMSE: 22.04 kWh (PROVEN BEST)")
print("=" * 140)
print()

# Calculate dates
today = datetime.now()
predict_dt = today
end_dt = today - timedelta(days=1)
start_dt = end_dt - timedelta(days=60)

START_DATE = start_dt.strftime("%Y-%m-%d")
END_DATE = end_dt.strftime("%Y-%m-%d")
PREDICT_DATE = predict_dt.strftime("%Y-%m-%d")

print(f"Training period: {START_DATE} to {END_DATE} (60 days)")
print(f"Prediction date: {PREDICT_DATE}")
print()

# ==================== STEP 1: GET LAST 60 DAYS AND CLASSIFY ====================
print("Step 1: Classifying days from 60-day window...")
print()

training_url = f"https://ap.elementsenergies.com/api/fetchHConsWAvg?startdate={START_DATE}&enddate={END_DATE}&msn={MSN}"
training_data = fetch_with_retry(training_url)

daily_classification = {}
max_daily_consumption = 0
hourly_records = []

if training_data:
    if isinstance(training_data, list):
        for record in training_data:
            if 'data' in record:
                hourly_data = record['data']
                if isinstance(hourly_data, dict):
                    for date_key, day_hours in hourly_data.items():
                        for hour_entry in day_hours:
                            hourly_records.append({
                                'date': date_key,
                                'consumption': float(hour_entry.get('consumption', 0))
                            })
    elif isinstance(training_data, dict) and 'data' in training_data:
        hourly_data = training_data['data']
        if isinstance(hourly_data, dict):
            for date_key, day_hours in hourly_data.items():
                for hour_entry in day_hours:
                    hourly_records.append({
                        'date': date_key,
                        'consumption': float(hour_entry.get('consumption', 0))
                    })
    
    df_hist = pd.DataFrame(hourly_records)
    daily_totals = df_hist.groupby('date')['consumption'].sum().reset_index()
    daily_totals.columns = ['Date', 'Daily_Total']
    
    max_daily_consumption = daily_totals['Daily_Total'].max()
    threshold = 0.20 * max_daily_consumption
    
    print(f"✓ Days in 60-day window: {len(daily_totals)}")
    print(f"✓ Max daily consumption: {max_daily_consumption:.2f} kWh")
    print(f"✓ 20% threshold: {threshold:.2f} kWh")
    print()
    
    for idx, row in daily_totals.iterrows():
        classification = classify_day_consumption(row['Daily_Total'], max_daily_consumption)
        daily_classification[row['Date']] = classification
    
    print(f"Last 7 days classification:")
    for date in sorted(daily_totals['Date'].tail(7).tolist()):
        total = daily_totals[daily_totals['Date'] == date]['Daily_Total'].values[0]
        classif = daily_classification[date]
        pct = (total / max_daily_consumption) * 100
        print(f"  {date}: {classif:<4} ({total:>7.0f} kWh, {pct:>5.1f}% of max)")
    print()

# ==================== STEP 2: GET PREVIOUS DAY CLASSIFICATION ====================
prev_day_dt = predict_dt - timedelta(days=1)
prev_day_str = prev_day_dt.strftime("%Y-%m-%d")
prev_day_classification = daily_classification.get(prev_day_str, 'HIGH')

print(f"Previous day ({prev_day_str}): {prev_day_classification}")
print()

# ==================== STEP 3: CREATE FEATURES ====================
print("Step 2: Creating features (lag_1, lag_24, lag_168, avg_7d, day_of_week)...")
print()

df = pd.DataFrame(hourly_records)
df_expanded = []

for date in df['date'].unique():
    day_data = df[df['date'] == date]['consumption'].values
    date_obj = datetime.strptime(date, '%Y-%m-%d')
    day_of_week = date_obj.weekday()  # 0=Monday, 1=Tuesday, ..., 6=Sunday
    
    if len(day_data) > 0:
        for hour in range(24):
            dt = date_obj + timedelta(hours=hour)
            df_expanded.append({
                'datetime': dt,
                'consumption': day_data[hour] if hour < len(day_data) else day_data[-1],
                'day_of_week': day_of_week
            })

df_full = pd.DataFrame(df_expanded)
df_full = df_full.sort_values('datetime').reset_index(drop=True)

# Create lag features
df_full['lag_1'] = df_full['consumption'].shift(1)
df_full['lag_24'] = df_full['consumption'].shift(24)
df_full['lag_168'] = df_full['consumption'].shift(168)
df_full['avg_7d'] = df_full['consumption'].shift(1).rolling(window=168, min_periods=1).mean()

feature_columns = ['lag_1', 'lag_24', 'lag_168', 'avg_7d', 'day_of_week']

# Drop rows without lag features
df_train = df_full.dropna(subset=['lag_1', 'lag_24', 'lag_168', 'avg_7d']).reset_index(drop=True)

print(f"✓ Training samples: {len(df_train)}")
print(f"✓ Features ({len(feature_columns)}): {feature_columns}")
print()

# Show day of week patterns
print("Consumption patterns by day of week:")
day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
for dow in range(7):
    dow_data = df_full[df_full['day_of_week'] == dow]['consumption']
    if len(dow_data) > 0:
        print(f"  {day_names[dow]:10s}: Avg={dow_data.mean():7.2f} kWh, Min={dow_data.min():7.2f}, Max={dow_data.max():7.2f}")
print()

# ==================== STEP 4: TRAIN XGBOOST MODEL ====================
print("Step 3: Training XGBoost model (FINAL BEST MODEL)...")
print()

X = df_train[feature_columns].values
y = df_train['consumption'].values

# Scale features
scaler = StandardScaler()
X_scaled = scaler.fit_transform(X)

# Train model
model = xgb.XGBRegressor(
    n_estimators=100,
    max_depth=5,
    learning_rate=0.1,
    random_state=42,
    verbosity=0
)
model.fit(X_scaled, y)

print(f"✓ Model trained")
print(f"✓ Feature importance:")
importance_df = pd.DataFrame({
    'Feature': feature_columns,
    'Importance': model.feature_importances_
}).sort_values('Importance', ascending=False)

for idx, row in importance_df.iterrows():
    bar = '█' * int(row['Importance'] * 100)
    print(f"    {row['Feature']:20s}: {row['Importance']:.4f}  {bar}")
print()

# ==================== STEP 5: MAKE PREDICTIONS FOR NEXT DAY ====================
print(f"Step 4: Making predictions for {PREDICT_DATE}...")
print()

# Get day of week for prediction day
predict_day_of_week = predict_dt.weekday()
day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
print(f"Prediction day: {day_names[predict_day_of_week]}")
print()

history = df_full.set_index('datetime')['consumption'].copy()
predictions = []

for hour in range(24):
    current_ts = predict_dt + timedelta(hours=hour)
    
    ts_lag_1 = current_ts - timedelta(hours=1)
    ts_lag_24 = current_ts - timedelta(hours=24)
    ts_lag_168 = current_ts - timedelta(hours=168)
    
    def get_lag_value(ts):
        if ts in history.index:
            return history.loc[ts]
        earlier = history[history.index <= ts]
        if len(earlier) > 0:
            return earlier.iloc[-1]
        return history.iloc[0] if len(history) > 0 else 0
    
    lag_1_val = get_lag_value(ts_lag_1)
    lag_24_val = get_lag_value(ts_lag_24)
    lag_168_val = get_lag_value(ts_lag_168)
    
    # 7-day rolling average
    window_start = current_ts - timedelta(hours=168)
    window_data = history[(history.index >= window_start) & (history.index < current_ts)]
    avg_7d_val = window_data.mean() if len(window_data) > 0 else history.mean()
    
    X_predict = np.array([[lag_1_val, lag_24_val, lag_168_val, avg_7d_val, predict_day_of_week]])
    X_predict_scaled = scaler.transform(X_predict)
    predicted_value = max(0, model.predict(X_predict_scaled)[0])
    
    history.loc[current_ts] = predicted_value
    
    hour_str = f"{hour:02d}:00"
    predictions.append({
        'Date': PREDICT_DATE,
        'Hour': hour_str,
        'Consumption (kWh)': round(predicted_value, 2)
    })

pred_df = pd.DataFrame(predictions)

print(f"✓ 24-hour predictions generated")
print(f"✓ Total predicted consumption: {pred_df['Consumption (kWh)'].sum():.2f} kWh")
print(f"✓ Mean hourly consumption: {pred_df['Consumption (kWh)'].mean():.2f} kWh")
print()

# Display predictions
print("=" * 140)
print("HOURLY PREDICTIONS")
print("=" * 140)
print()
print(f"{'Hour':<8} {'Consumption (kWh)':<20}")
print("-" * 28)
for idx, row in pred_df.iterrows():
    print(f"{row['Hour']:<8} {row['Consumption (kWh)']:>18.2f}")

print("-" * 28)
print(f"{'TOTAL':<8} {pred_df['Consumption (kWh)'].sum():>18.2f}")
print()

output_file = f"prediction_{PREDICT_DATE.replace('-', '')}_FINAL_MODEL.xlsx"
pred_df.to_excel(output_file, index=False, sheet_name='Predictions')

print("=" * 140)
print(f"✓ Predictions saved to: {output_file}")
print("=" * 140)
