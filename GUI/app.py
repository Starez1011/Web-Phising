from flask import Flask, render_template, request
import FeatureExtraction
import pickle
import warnings
import xgboost as xgb
import pandas as pd
from urllib.parse import urlparse
import os
import requests
from requests.exceptions import RequestException, SSLError, ConnectionError, Timeout
import socket
import urllib3
import pyshorteners

app = Flask(__name__, static_url_path='/static')

# Suppress warnings
warnings.filterwarnings('ignore')

# Create instances
feature_extractor = FeatureExtraction.FeatureExtraction()

# Suppress SSL verification warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def get_model_path(model_name):
    """
    Get the correct path for model files regardless of how the application is run
    """
    # Get the directory where the current file (app.py) is located
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(current_dir, model_name)

def check_url_connectivity(url):
    """
    Check if the URL is accessible and has valid HTTPS connection
    Returns: (bool, str) - (is_accessible, message)
    """
    try:
        # Add https:// if not present
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        # Set a reasonable timeout
        timeout = 15
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        # First try to resolve the domain
        domain = urlparse(url).netloc.lower()
        try:
            socket.gethostbyname(domain)
        except socket.gaierror:
            return False, "Domain name could not be resolved. Please check if the URL is correct."

        # Try HTTPS connection first with SSL verification disabled
        try:
            response = requests.get(url, timeout=timeout, headers=headers, verify=False)
            if response.status_code == 200:
                return True, "Website is accessible"
            else:
                # Try with www. prefix
                if not domain.startswith('www.'):
                    www_url = url.replace('://', '://www.')
                    try:
                        response = requests.get(www_url, timeout=timeout, headers=headers, verify=False)
                        if response.status_code == 200:
                            return True, "Website is accessible with www prefix"
                    except:
                        pass
                
                return False, f"Website returned status code {response.status_code}. The website might be temporarily unavailable."
        except SSLError:
            # If SSL fails, try HTTP
            try:
                url = url.replace('https://', 'http://')
                response = requests.get(url, timeout=timeout, headers=headers)
                if response.status_code == 200:
                    return True, "Website is accessible via HTTP"
                else:
                    return False, f"Website returned status code {response.status_code}. The website might be temporarily unavailable."
            except RequestException:
                return False, "Website is not accessible. The domain might be inactive or the server is not responding."
    except (ConnectionError, Timeout):
        return False, "Could not connect to the website. The domain might be inactive or the server is not responding."
    except Exception as e:
        print(f"Connection error details: {str(e)}")  # Print detailed error for debugging
        return False, f"Error checking website: {str(e)}"

# Load XGBoost model
try:
    xgb_model = xgb.XGBClassifier(
        n_estimators=100,
        learning_rate=0.1,
        max_depth=5,
        random_state=42,
        tree_method='hist',
        n_jobs=1
    )
    xgb_model_path = get_model_path('XGBoostModel_12000.sav')
    xgb_model.load_model(xgb_model_path)
    print("XGBoost model loaded successfully")
except Exception as e:
    print(f"Error loading XGBoost model: {e}")
    xgb_model = None

# Load Random Forest model
try:
    rf_model_path = get_model_path('RFmodel_12000.sav')
    with open(rf_model_path, 'rb') as f:
        rf_model = pickle.load(f)
    print("Random Forest model loaded successfully")
except Exception as e:
    print(f"Error loading Random Forest model: {e}")
    rf_model = None

def preprocess_data(data):
    """Preprocess the data to match model's expected format"""
    # Ensure all required features are present
    required_features = [
        'long_url',
        'having_@_symbol',
        'redirection_//_symbol',
        'prefix_suffix_seperation',
        'sub_domains',
        'having_ip_address',
        'shortening_service',
        'https_token',
        'web_traffic',
        'domain_registration_length',
        'dns_record',
        'age_of_domain',
        'statistical_report'
    ]
    
    # Drop non-numeric columns
    numeric_data = data.select_dtypes(include=['int64', 'float64'])
    
    # Handle any missing values
    numeric_data = numeric_data.fillna(0)
    
    # Ensure all required features are present
    for feature in required_features:
        if feature not in numeric_data.columns:
            numeric_data[feature] = 0
    
    # Reorder columns to match model's expected order
    return numeric_data[required_features]

@app.route('/')
def index():
    return render_template("home.html")

@app.route('/about')
def about():
    return render_template("about.html")

def get_reasons(features):
    reasons = []
    feature_descriptions = {
        'long_url': 'URL length is suspiciously long',
        'having_@_symbol': 'URL contains @ symbol (high risk)',
        'redirection_//_symbol': 'URL contains suspicious redirection (high risk)',
        'prefix_suffix_seperation': 'Domain contains hyphens',
        'sub_domains': 'URL has multiple subdomains',
        'having_ip_address': 'URL contains IP address (high risk)',
        'shortening_service': 'URL uses URL shortening service (high risk)',
        'https_token': 'URL has suspicious HTTPS tokens',
        'web_traffic': 'Suspicious web traffic patterns',
        'domain_registration_length': 'Domain registration period is short',
        'dns_record': 'No DNS record found',
        'age_of_domain': 'Domain is very new',
        'statistical_report': 'Statistical analysis indicates suspicious patterns'
    }
    
    url = request.form['url']
    
    # Check for number-for-letter substitutions
    number_letter_map = {
        '0': 'o',
        '1': 'i',
        '3': 'e',
        '4': 'a',
        '5': 's',
        '7': 't',
        '8': 'b'
    }
    
    suspicious_chars = []
    for char in url:
        if char in number_letter_map:
            suspicious_chars.append(f"'{char}' (mimicking '{number_letter_map[char]}')")
    
    if suspicious_chars:
        reasons.append("⚠️ HIGH RISK: This URL uses numbers to mimic letters")
        reasons.append("Suspicious character substitutions detected:")
        for char in suspicious_chars:
            reasons.append(f"- {char}")
        reasons.append("This is a common phishing technique to make malicious URLs look legitimate")
    
    # Check typo-squatting
    typo_check = feature_extractor.check_typo_squatting(request.form['url'])
    if typo_check['is_typo_squatting']:
        reasons.append(f"⚠️ HIGH RISK: This appears to be a typo-squatting attempt")
        reasons.append(f"This domain is trying to impersonate {typo_check['company_name']}'s official website ({typo_check['original_domain']})")
        reasons.append("Common typo-squatting techniques detected:")
        reasons.append("- Using numbers instead of letters (e.g., '0' instead of 'o')")
        reasons.append("- Using similar-looking characters")
        reasons.append("- Slight misspellings of the original domain")
        reasons.append(f"Please visit the official website: {typo_check['original_domain']}")
        return reasons
    
    # Add warning for suspicious TLD or domain
    if feature_extractor.check_suspicious_tld(request.form['url']):
        reasons.append("WARNING: This website uses a suspicious top-level domain commonly associated with malicious sites")
    if feature_extractor.check_suspicious_domain(request.form['url']):
        reasons.append("WARNING: This domain contains suspicious patterns that may indicate phishing")
    
    # Show feature warnings based on ML model features
    for feature, value in features.items():
        if value == 1 and feature in feature_descriptions:
            reasons.append(feature_descriptions[feature])
    
    # Add additional context
    if '.gov' in request.form['url']:
        reasons.append("This appears to be a government website (.gov domain)")
    elif not reasons:
        reasons.append("No suspicious features detected")
    
    return reasons

def is_trusted_domain(url):
    """Check if the domain is from a trusted TLD"""
    trusted_tlds = ['.gov.np', '.edu.np']
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    return any(domain.endswith(tld) for tld in trusted_tlds)

@app.route('/getURL', methods=['GET', 'POST'])
def getURL():
    if request.method == 'POST':
        url = request.form['url']
        print(f"\nProcessing URL: {url}")
        
        # Validate URL first
        is_valid, result = feature_extractor.validate_url(url)
        if not is_valid:
            return render_template("home.html", error="Invalid URL", reasons=[result])
        
        # Use the validated URL
        url = result
        print(f"Validated URL: {url}")
        
        # Check if it's a shortened URL and resolve it
        original_url = url
        final_url = None
        is_shortened = False
        suspicious_redirect = False
        redirect_domain = None
        
        try:
            # First try to resolve using requests with redirects
            response = requests.head(url, allow_redirects=True, timeout=10)
            final_url = response.url
            
            # If the URL is different, it was a redirect
            is_shortened = final_url != original_url
            
            if is_shortened:
                print(f"Shortened URL detected. Original: {original_url}")
                print(f"Final destination: {final_url}")
                # Use the final URL for all subsequent checks
                url = final_url
                redirect_domain = urlparse(final_url).netloc.lower()
                
        except Exception as e:
            print(f"Error resolving shortened URL: {str(e)}")
            # Extract the domain from the error message if possible
            error_str = str(e)
            if "host='" in error_str and "'" in error_str.split("host='")[1]:
                redirect_domain = error_str.split("host='")[1].split("'")[0].lower()
                is_shortened = True
                final_url = f"https://{redirect_domain}"
                print(f"Detected redirect domain from error: {redirect_domain}")
            
            # If direct resolution fails, try using pyshorteners
            try:
                shortener = pyshorteners.Shortener()
                final_url = shortener.expand(url)
                if final_url and final_url != url:
                    is_shortened = True
                    print(f"Shortened URL detected via pyshorteners. Original: {original_url}")
                    print(f"Final destination: {final_url}")
                    url = final_url
                    redirect_domain = urlparse(final_url).netloc.lower()
            except Exception as e:
                print(f"Error using pyshorteners: {str(e)}")
                # If both methods fail, proceed with original URL
                if not redirect_domain:
                    is_shortened = False
                    final_url = url
        
        # Check for suspicious patterns in the redirect domain
        if redirect_domain:
            suspicious_keywords = [
                'login', 'signin', 'sign-in', 'account', 'secure', 'verify', 'confirm',
                'update', 'validate', 'security', 'password', 'bank', 'paypal', 'amazon',
                'ebay', 'apple', 'microsoft', 'google', 'facebook', 'twitter', 'instagram'
            ]
            suspicious_redirect = any(keyword in redirect_domain for keyword in suspicious_keywords)
            
            if suspicious_redirect:
                print(f"\nSuspicious redirect detected to domain containing: {[k for k in suspicious_keywords if k in redirect_domain]}")
        
        # Check URL connectivity
        is_accessible, connectivity_message = check_url_connectivity(url)
        if not is_accessible:
            # Store the connectivity message to show later
            if suspicious_redirect:
                detected_patterns = [k for k in suspicious_keywords if k in redirect_domain]
                pattern_text = " and ".join(detected_patterns)
                
                # Build warning message dynamically
                warning_parts = []
                warning_parts.append("⚠️ HIGH RISK: This is a phishing attempt!")
                warning_parts.append(f"• Redirects to a suspicious {pattern_text}")
                warning_parts.append(f"• Website is inaccessible (common in phishing)")
                warning_parts.append(f"• Uses URL shortener to hide destination")
                
                connectivity_warning = "\n".join(warning_parts)
            else:
                # Build general warning message dynamically
                warning_parts = []
                warning_parts.append("⚠️ WARNING: This website is currently inaccessible.")
                warning_parts.append(f"🔗 Original link: {original_url}")
                warning_parts.append(f"🎯 Attempted destination: {final_url}")
                warning_parts.append("💡 This could be due to:")
                warning_parts.append("• The website being temporarily down")
                warning_parts.append("• The website no longer existing")
                warning_parts.append("• Network connectivity issues")
                
                connectivity_warning = "\n\n".join(warning_parts)
        else:
            connectivity_warning = None
        
        if xgb_model is None or rf_model is None:
            return render_template("home.html", error="Error: One or more models not loaded properly")
        
        try:
            # Get features and make ML prediction
            data, phishing_reasons = feature_extractor.getAttributess(url)
            data = preprocess_data(data)
            
            # Get probabilities from both models
            xgb_proba = xgb_model.predict_proba(data)[0]
            rf_proba = rf_model.predict_proba(data)[0]
            
            # Calculate weighted probabilities (60% XGBoost, 40% Random Forest)
            weighted_proba = (0.6 * xgb_proba) + (0.4 * rf_proba)
            
            # Adjust probability based on suspicious redirects
            if suspicious_redirect:
                # Increase phishing probability by 40% (but cap at 0.95)
                weighted_proba[1] = min(0.95, weighted_proba[1] + 0.4)
                weighted_proba[0] = 1 - weighted_proba[1]
                
                print("\nAdjusted Probabilities (after suspicious redirect detection):")
                print(f"  - Legitimate: {weighted_proba[0]*100:.1f}%")
                print(f"  - Phishing: {weighted_proba[1]*100:.1f}%")
            
            # Print detailed probabilities in backend
            print("\nModel Probabilities:")
            print("-------------------")
            print(f"XGBoost Model:")
            print(f"  - Legitimate: {xgb_proba[0]*100:.1f}%")
            print(f"  - Phishing: {xgb_proba[1]*100:.1f}%")
            print(f"\nRandom Forest Model:")
            print(f"  - Legitimate: {rf_proba[0]*100:.1f}%")
            print(f"  - Phishing: {rf_proba[1]*100:.1f}%")
            print(f"\nCombined Weighted Probability (60% XGBoost, 40% RF):")
            print(f"  - Legitimate: {weighted_proba[0]*100:.1f}%")
            print(f"  - Phishing: {weighted_proba[1]*100:.1f}%")
            print("-------------------")
            
            # Get features for display
            features = data.iloc[0].to_dict()
            
            # Determine final result based on weighted probability
            is_phishing = weighted_proba[1] > 0.5  # If probability of phishing > 0.5
            confidence = "HIGH" if abs(weighted_proba[1] - 0.5) > 0.3 else "MEDIUM"
            trust_level = "HIGH" if is_trusted_domain(url) else "NORMAL"
            
            # Override classification if suspicious redirect is detected
            if suspicious_redirect:
                is_phishing = True
                confidence = "HIGH"
                value = f"⚠️ {confidence} RISK: This URL is Phishing"
                # Combine all reasons for suspicious URLs, using set to prevent duplicates
                reasons = set()
                
                # Add connectivity warning if website is not accessible
                if connectivity_warning:
                    reasons.add(connectivity_warning)
                
                # Add shortened URL information if applicable
                if is_shortened:
                    reasons.add(f"⚠️ WARNING: This is a shortened URL")
                    reasons.add(f"Final destination: {final_url}")
                
                # Add domain trust information if applicable
                if trust_level == "HIGH":
                    reasons.add("⚠️ WARNING: This URL is on a trusted domain (.gov.np/.edu.np) but shows suspicious behavior. Even trusted domains can be compromised.")
                
                if phishing_reasons:
                    reasons.update(phishing_reasons)
                if not reasons:
                    reasons.add("Multiple indicators suggest this is a phishing website")
                reasons = list(reasons)  # Convert set back to list for template
            else:
                # Prepare the response
                if is_phishing:
                    value = f"⚠️ {confidence} RISK: This URL is Phishing"
                    # Combine all reasons for suspicious URLs, using set to prevent duplicates
                    reasons = set()
                    
                    # Add connectivity warning if website is not accessible
                    if connectivity_warning:
                        reasons.add(connectivity_warning)
                    
                    # Add shortened URL information if applicable
                    if is_shortened:
                        reasons.add(f"⚠️ WARNING: This is a shortened URL")
                        reasons.add(f"Final destination: {final_url}")
                    
                    # Add domain trust information if applicable
                    if trust_level == "HIGH":
                        reasons.add("⚠️ WARNING: This URL is on a trusted domain (.gov.np/.edu.np) but shows suspicious behavior. Even trusted domains can be compromised.")
                    
                    if phishing_reasons:
                        reasons.update(phishing_reasons)
                    if not reasons:
                        reasons.add("Multiple indicators suggest this is a phishing website")
                    reasons = list(reasons)  # Convert set back to list for template
                else:
                    if trust_level == "HIGH":
                        value = "This URL is Legitimate"
                        reasons = [f"This is an official website on a trusted domain ({urlparse(url).netloc})"]
                    else:
                        value = "This URL is Legitimate"
                        reasons = []  # No reasons for legitimate URLs
                    
                    # Add connectivity warning if website is not accessible
                    if connectivity_warning:
                        reasons.append(connectivity_warning)
                    
                    # Add shortened URL information even for legitimate URLs
                    if is_shortened:
                        reasons.append(f"This is a shortened URL")
                        reasons.append(f"Final destination: {final_url}")
            
            return render_template("home.html", error=value, reasons=reasons)
            
        except Exception as e:
            print(f"Error during prediction: {str(e)}")
            return render_template("home.html", error=f"Error during prediction: {str(e)}")

if __name__ == "__main__":
    app.run(debug=True)