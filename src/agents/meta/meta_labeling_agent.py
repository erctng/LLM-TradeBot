import os
import joblib
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional
import logging

try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import LabelEncoder
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

log = logging.getLogger(__name__)

class MetaLabelingAgent:
    """
    Second Brain / Meta-Labeling Agent.
    
    Instead of predicting market direction, this model predicts if the 
    primary strategy's (LLM) decision will be profitable or not.
    """
    
    def __init__(self, model_dir: str = "data/models/meta_labeling"):
        self.model_dir = model_dir
        self.model_path = os.path.join(model_dir, "rf_meta_model.pkl")
        self.encoder_path = os.path.join(model_dir, "label_encoders.pkl")
        
        self.model = None
        self.encoders = {}
        self.is_trained = False
        
        if not os.path.exists(model_dir):
            os.makedirs(model_dir)
            
        self._load_model()
        
    def _load_model(self):
        """Load pre-trained model if available"""
        if SKLEARN_AVAILABLE and os.path.exists(self.model_path) and os.path.exists(self.encoder_path):
            try:
                self.model = joblib.load(self.model_path)
                self.encoders = joblib.load(self.encoder_path)
                self.is_trained = True
                log.info("🧠 Meta-Labeling model loaded successfully.")
            except Exception as e:
                log.warning(f"⚠️ Failed to load Meta-Labeling model: {e}")
                self.is_trained = False
        else:
            self.model = RandomForestClassifier(
                n_estimators=100, 
                max_depth=5,
                class_weight='balanced',
                random_state=42
            ) if SKLEARN_AVAILABLE else None

    def _extract_features(self, decision: Dict, context: Dict = None) -> Dict:
        """Extract features from the decision and context"""
        features = {}
        
        # Action (1 for Long, -1 for Short, 0 for others)
        action_str = decision.get('action', '').lower()
        if 'long' in action_str:
            features['action_type'] = 1
        elif 'short' in action_str:
            features['action_type'] = -1
        else:
            features['action_type'] = 0
            
        features['confidence'] = decision.get('confidence', 50.0)
        
        # Time features
        now = datetime.now()
        features['hour'] = now.hour
        features['day_of_week'] = now.weekday()
        
        # Market context (if provided)
        if context:
            features['volatility'] = context.get('volatility_atr_pct', 0.0)
            # regime encoding
            regime = str(context.get('regime', 'unknown'))
            if 'regime' not in self.encoders:
                self.encoders['regime'] = LabelEncoder()
                # Dummy fit just to have something
                self.encoders['regime'].fit(['unknown', 'trending_up', 'trending_down', 'sideways', 'high_volatility', regime])
            
            try:
                features['regime_encoded'] = self.encoders['regime'].transform([regime])[0]
            except ValueError:
                # Unseen label
                features['regime_encoded'] = self.encoders['regime'].transform(['unknown'])[0]
        else:
            features['volatility'] = 0.0
            features['regime_encoded'] = 0
            
        return features

    def train(self, trades_history: List[Dict]):
        """
        Train the Meta-Labeling model on historical trades.
        Trades must contain: 'pnl', 'action', 'confidence', 'hour', 'regime', 'volatility', etc.
        """
        if not SKLEARN_AVAILABLE:
            log.warning("sklearn not installed. Cannot train Meta-Labeling model.")
            return False
            
        if len(trades_history) < 30:
            log.warning(f"Not enough trades to train meta-labeling. Need >=30, got {len(trades_history)}.")
            return False
            
        X_list = []
        y_list = []
        
        # Fit encoders first
        regimes = [t.get('regime', 'unknown') for t in trades_history]
        self.encoders['regime'] = LabelEncoder()
        self.encoders['regime'].fit(regimes + ['unknown'])

        for t in trades_history:
            # We only train on opening trades that have been closed and have a PnL
            if 'pnl' not in t or t['pnl'] is None:
                continue
                
            features = {
                'action_type': 1 if 'long' in str(t.get('action')).lower() else (-1 if 'short' in str(t.get('action')).lower() else 0),
                'confidence': t.get('confidence', 50.0),
                'hour': t.get('hour', 12),
                'day_of_week': t.get('day_of_week', 0),
                'volatility': t.get('volatility', 0.0),
                'regime_encoded': self.encoders['regime'].transform([t.get('regime', 'unknown')])[0]
            }
            
            X_list.append(features)
            # Label: 1 if profitable, 0 if not
            y_list.append(1 if float(t['pnl']) > 0 else 0)
            
        if len(X_list) < 30:
            log.warning("Not enough valid closed trades for training.")
            return False
            
        df_X = pd.DataFrame(X_list)
        y = np.array(y_list)
        
        # Train model
        self.model.fit(df_X, y)
        self.is_trained = True
        
        # Save model
        joblib.dump(self.model, self.model_path)
        joblib.dump(self.encoders, self.encoder_path)
        log.info(f"🧠 Meta-Labeling model trained on {len(X_list)} trades and saved.")
        return True

    def predict_success_probability(self, decision: Dict, context: Dict = None) -> float:
        """
        Predict the probability (0.0 to 1.0) that the trade will be profitable.
        """
        if not SKLEARN_AVAILABLE or not self.is_trained or self.model is None:
            return 0.5 # Neutral probability if not trained
            
        features = self._extract_features(decision, context)
        df_X = pd.DataFrame([features])
        
        try:
            # Predict probability of class 1 (profitable)
            proba = self.model.predict_proba(df_X)[0][1]
            return float(proba)
        except Exception as e:
            log.warning(f"Meta-label prediction failed: {e}")
            return 0.5
