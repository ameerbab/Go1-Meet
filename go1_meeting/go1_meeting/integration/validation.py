import frappe,msal,requests,json,base64,urllib.parse
import jwt,time
# from go1_meeting.go1_meeting.doctype.meeting_integration.meeting_integration import create_room
# from go1_meeting.go1_meeting.doctype.meeting_integration.meeting_integration import create_meeting_link
@frappe.whitelist()
def validate_user(doc):
    doc = json.loads(doc)
    if not frappe.db.exists("User Platform Credentials",{"user":frappe.session.user,"platform":doc['platform']}):
        return "invalid Credentials"
    
#Fetch users to validate the teams access token
@frappe.whitelist()
def fetch_users(access_token):
    headers = {"Authorization": "Bearer " + access_token}
    user_directory = requests.get(
			url ="https://graph.microsoft.com/v1.0/users",
			headers=headers
		)
    return user_directory.json()


def create_access_token_from_refresh_token(platform,refresh_token):
    if platform == "Teams":
        teams_credential = frappe.get_doc("Meeting Integration",{"platform":"Teams"})
        client_id = teams_credential.client_id
        client_secret = teams_credential.get_password("client_secret")
        tenant_id = teams_credential.tenant_id
        authority = f"https://login.microsoftonline.com/{tenant_id}"
        scopes = ['User.Read','User.Read.All', 'OnlineMeetings.ReadWrite']
        msal_app = msal.ConfidentialClientApplication(
        client_id,
        authority=authority,
        client_credential=client_secret
        )

        token_response = msal_app.acquire_token_by_refresh_token(refresh_token, scopes=scopes)
        return token_response
    if platform == "Google Meet":
        token_url = 'https://oauth2.googleapis.com/token'
        gmeet_cred = frappe.get_doc("Meeting Integration",{"platform":"Google Meet"})
        payload = {
            'grant_type': 'refresh_token',
            'client_id': gmeet_cred.get_password("client_id"),
            'client_secret': gmeet_cred.get_password("client_secret"),
            'refresh_token': refresh_token
        }
        refresh_resp = requests.post(url = token_url, data = payload)
        frappe.log_error("refresh token",refresh_resp.json())
        if refresh_resp.status_code == 200:
            set_token_response(refresh_resp.json(),platform = "Google Meet",user = "Administrator")
            return {'status':"success",'message':refresh_resp.json()}

@frappe.whitelist()
def _redirect_uri(doc):
    if doc['platform'] == "Teams":
        client_id , client_secret , tenant_id , scopes = get_teams_credentials()
        authority = f"https://login.microsoftonline.com/{tenant_id}"
        msal_app = msal.ClientApplication(client_id, authority=authority, client_credential=client_secret)
        redirect_uri = frappe.utils.get_url('/api/method/go1_meeting.go1_meeting.integration.validation.teams_oauth_callback')
        frappe.log_error("redirect_uri",redirect_uri)
        # Generate authorization URL
        auth_url = msal_app.get_authorization_request_url(scopes, redirect_uri=redirect_uri,state = doc['name'])
        frappe.log_error("auth_url",auth_url)
    elif doc['platform'] == "Zoom":
        client_id = frappe.db.get_value("Meeting Integration",{'platform':doc['platform']},['client_id'])
        redirect_uri = frappe.utils.get_url('/api/method/go1_meeting.go1_meeting.integration.validation.zoom_oauth_callback')
        encoded_state = urllib.parse.urlencode({"name":doc['name']})
        auth_url = f"https://zoom.us/oauth/authorize?response_type=code&client_id={client_id}&redirect_uri={redirect_uri}&state={encoded_state}"
        frappe.log_error("auth_url",auth_url)
    return auth_url

@frappe.whitelist(allow_guest = True)
def teams_oauth_callback(code = None,state = None):
    if not code:
        frappe.throw("Authorization code not found")
    client_id , client_secret , tenant_id , scopes = get_teams_credentials()
    frappe.log_error("teams credentials",[client_id , client_secret , tenant_id , scopes])
    redirect_uri = frappe.utils.get_url('/api/method/go1_meeting.go1_meeting.integration.validation.teams_oauth_callback')
    authority = f"https://login.microsoftonline.com/{tenant_id}"
    frappe.log_error("code",code)
    frappe.log_error("state",state)
    msal_app = msal.ConfidentialClientApplication(
        client_id,
        authority=authority,
        client_credential=client_secret
    )
    token_response = msal_app.acquire_token_by_authorization_code(
        code=code,
        scopes=scopes,
        redirect_uri=redirect_uri
    )
    frappe.log_error("token response",token_response)
    set_token_response(token_response,platform = "Teams")
    frappe.log_error("token resp from microsoft",token_response)
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = f"/app/go1-meet/{state}"

@frappe.whitelist(allow_guest = True) 
def zoom_oauth_callback(code = None):
    dict_code = frappe.form_dict.get("code")
    state = frappe.form_dict.get("state")
    frappe.log_error("code",code)
    frappe.log_error("dict code",dict_code)
    if state:
        query_state = urllib.parse.parse_qs(state)
        doc_name = query_state.get("name")[0]
    zoom_credentials = frappe.get_doc("Meeting Integration",{"platform":"Zoom"})
    client_id = zoom_credentials.client_id
    client_secret = zoom_credentials.get_password("client_secret")
    frappe.log_error("client id ans secret",[client_id,client_secret])
    token_url = "https://zoom.us/oauth/token"
    redirect_uri = frappe.utils.get_url('/api/method/go1_meeting.go1_meeting.integration.validation.zoom_oauth_callback')
    headers = {
        "Authorization": "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode(),
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type":"account_credentials",
        "account_id":11234
    }
    response = requests.post(token_url, headers=headers, json = data)
    frappe.log_error('response sts code',response.status_code)
    frappe.log_error("zoom access token",response.text)
    if response.status_code == 200:
        token_data = response.json()
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = f"/app/app/go1-meet/{doc_name}?state=authorized"

@frappe.whitelist(allow_guest = True)
def google_oauth_callback(code=None):
    code = frappe.form_dict.get("code")
    encoded_state = frappe.form_dict.get("state")
    state_data = dict(urllib.parse.parse_qsl(encoded_state))
    frappe.log_error("code",code)
    frappe.log_error("state",state_data)
    exchange_token_url=f"https://oauth2.googleapis.com/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "code" : code,
        "client_id" : state_data.get("client_id"),
        "client_secret":state_data.get("client_secret"),
        "redirect_uri" : frappe.utils.get_url('/api/method/go1_meeting.go1_meeting.integration.validation.google_oauth_callback'),
        "grant_type" : "authorization_code"
    }
    frappe.log_error("g data",data)
    response = requests.post(exchange_token_url, headers=headers, data=data)
    frappe.log_error()
    if response.status_code == 200:
        if response.json().get("access_token"):
            frappe.log_error("gaccess_toke",response.json())
            if not frappe.db.exists("Google Calendar",{"user":"Administrator"}):
                create_calendar(response.json(),state_data.get('doc'))
            set_token_response(response.json(),"Google Meet",user="Administrator")
            frappe.local.response["type"] = "redirect"
            frappe.local.response["location"] = f"/app/meeting-integration/{state_data.get('doc')}"

@frappe.whitelist()
def authorize_user_access_token(doc):
    if type(doc) == str:
        doc = json.loads(doc)
    #Authorize zoom
    if doc['platform'] == "Zoom":
        return authorize_zoom(doc)
    #Authenticate Teams meeting
    if doc['platform'] == "Teams":
        user = frappe.session.user
        if not frappe.db.exists("User Platform Credentials",{"user":user,"platform":doc['platform']}):
            auth_url = _redirect_uri(doc)
            return {"status":"not_authorized","message":auth_url}
        else:
            return {"status":"authorized"}
    if doc['platform'] == "Google Meet":
        if doc['doctype'] == "Go1 Meet":
            gdoc = frappe.db.exists("Meeting Integration",{"platform":"Google Meet"})
            if not gdoc:
                frappe.throw("Enter Credentials for Google Meet in Meeting Integration")
        if doc['doctype'] == "Meeting Integration":
            return authorize_google(doc)
        return validate_gmeet_user(doc)
    if doc['platform'] == "WhereBy":
        whereby_cred = frappe.db.exists("Meeting Integration",{'platform':doc['platform']})
        if not whereby_cred:
            return {
                "status":"failed",
                "message":"not authorized"
            }
        return{"status":"success","message":"authorized"}
def get_teams_credentials():
    exists = frappe.db.exists("Meeting Integration",{"platform":"Teams"})
    if exists:
        cred_doc = frappe.get_doc("Meeting Integration",{"platform":"Teams"})
        client_id = cred_doc.client_id
        client_secret = cred_doc.get_password("client_secret")
        tenant_id = cred_doc.tenant_id
        scopes = ['User.Read', 'OnlineMeetings.ReadWrite']
        return client_id,client_secret,tenant_id,scopes
    else:
        frappe.throw("Enter the credentials for Teams in Meeting Integration")


def create_calendar(token_respose,doc_name):
    calendar_url = 'https://www.googleapis.com/calendar/v3/calendars'
    calendar_data = {
        "summary": "Go1 Meeting",
        "description": "A calendar for go1 meeting",
        "timeZone": frappe.db.get_value("User",frappe.session.user,"time_zone")
    }
    headers = {"Authorization":f"Bearer {token_respose['access_token']}",
               "Content-Type": "application/json"}
    cal_response = requests.post(calendar_url,headers = headers,json = calendar_data)
    frappe.log_error("cal json",cal_response.json())
    if cal_response.status_code == 200:
        gcal = frappe.get_doc("Meeting Integration",doc_name)
        gcal.calendar_name = cal_response.json().get("summary")
        gcal.calendar_id = cal_response.json().get("id")
        gcal.save()
        frappe.db.commit()

def validate_gmeet_user(doc):
    if type(doc) == str:
        doc = json.loads(doc)
    user = "Administrator"
    frappe.log_error("doc plat",doc['platform'])
    cred = frappe.db.exists("User Platform Credentials",{"user":user,"platform":doc['platform']})
    if not cred:
        return {"status":"not_authorized"}
    cred_doc = frappe.get_doc("User Platform Credentials",cred)
    headers = {"Authorization":f"Bearer {cred_doc.get('access_token')}",
               "Content-Type": "application/json"}
    cal_list = requests.get("https://www.googleapis.com/calendar/v3/users/me/calendarList",headers = headers)
    frappe.log_error("cal list sts",cal_list.status_code)
    frappe.log_error("cal list json",cal_list.json())
    if cal_list.status_code == 200:
        return {"status":"authorized"} 
    
    resp = create_access_token_from_refresh_token("Google Meet",cred_doc.refresh_token)
    frappe.log_error("ref resp",resp)
    if 'access_token' in resp.get('message'):
        return {"status":"authorized"}
    else:
        return {"status":"not_authorized","message":"Kindly Check the Client Credentials"}

def set_token_response(token_response,platform,user=None):
    cur_user = frappe.session.user if not user else user
    frappe.log_error("cur user",cur_user)
    frappe.log_error("token_response set tokens",type(token_response))
    token_doc = frappe.db.exists("User Platform Credentials",{"user":cur_user,"platform":platform})
    frappe.log_error("token not doc","No doc" if not token_doc else "Doc")
    if not token_doc:
        frappe.log_error("set token inside",type(token_response))
        cred = frappe.get_doc({
                "doctype": "User Platform Credentials",
                "user":cur_user,
                "platform":platform,
                "access_token":token_response['access_token'],
                "refresh_token":token_response['refresh_token'] if "refresh_token" in token_response else None
            })
        cred.insert()
        frappe.db.commit()
    else:
        frappe.log_error("set token else",f"Doc available {token_doc}")
        cred = frappe.get_doc("User Platform Credentials",token_doc)
        cred.access_token = token_response['access_token']
        if "refresh_token" in token_response:
            cred.refresh_token = token_response['refresh_token']
        cred.save()
        frappe.db.commit()

def generate_zoom_token(doc):
    zoom_doc = frappe.get_doc("Meeting Integration",{"platform":doc['platform']})
    client_id = zoom_doc.client_id
    client_secret = zoom_doc.get_password("client_secret")
    account_id = zoom_doc.account_id
    token_url = "https://zoom.us/oauth/token"
    headers = {
        "Authorization": "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode(),
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type":"account_credentials",
        "account_id":account_id
    }
    frappe.log_error("cred",[client_id,client_secret,account_id])
    frappe.log_error("headers",headers)
    frappe.log_error("data",data)
    access_response = requests.post(token_url, headers=headers, data = data)
    frappe.log_error("access token",access_response.json())
    if access_response.status_code == 200:
        set_token_response(access_response.json(),doc['platform'],user="Administrator")
        return authorize_zoom(doc)

@frappe.whitelist()
def authorize_zoom(doc):
    if type(doc) ==  str:
        doc = json.loads(doc)
    if frappe.db.exists("Meeting Integration",{"platform":"Zoom"}):
        if not frappe.db.exists("User Platform Credentials",
                                {"user":"Administrator",
                                "platform":doc['platform']}):
            #Generate access token
            return generate_zoom_token(doc)
        else:
            credentials = frappe.get_doc("User Platform Credentials",
                                        {"user":"Administrator",
                                        "platform":doc['platform']
                                        })
            if credentials.get('access_token'):
                validate_url = "https://api.zoom.us/v2/users/me"
                headers={
                    "Authorization": "Bearer " + credentials.get('access_token'),
                    "Content-Type": "application/json"
                }
                response = requests.get(url=validate_url,headers=headers)
                frappe.log_error("autorize zoom",response.status_code)
                if response.status_code != 200:
                    frappe.log_error("returning staus !200",generate_zoom_token(doc))
                    return generate_zoom_token(doc)
                frappe.log_error("returning",credentials.get('access_token'))
                return {
                    "status":"success",
                    "message":"authorized",
                    "access_token":credentials.get('access_token'),
                    "auth_response":response.json()
                }
    else:
        frappe.throw("Enter the credentials for Zoom in Meeting Integration")

def authorize_google(doc):
    google_meet = frappe.get_doc("Meeting Integration",{"platform":doc['platform']})
    client_id = google_meet.client_id
    client_secret = google_meet.get_password("client_secret")
    oauth_url = f"https://accounts.google.com/o/oauth2/v2/auth"
    state_data = {
        "client_id":client_id,
        "client_secret":client_secret,
        "doc":doc['name']
    }
    encode_state = urllib.parse.urlencode(state_data)
    frappe.log_error("endoded state",encode_state)
    data = {
        "access_type" : "offline",
        "client_id":client_id,
        "response_type":"code",
        "redirect_uri":frappe.utils.get_url("/api/method/go1_meeting.go1_meeting.integration.validation.google_oauth_callback"),
        "scope":"https://www.googleapis.com/auth/calendar https://www.googleapis.com/auth/calendar.events",
        "access_type": "offline",
        "state":encode_state,
        "prompt" : "consent"
    }
    frappe.log_error("auth url auth google",f"{oauth_url}?{urllib.parse.urlencode(data)}")
    # auth = requests.post(url = oauth_url,data = data)
    # frappe.log_error("auth",auth.json())
    return {"status":"success","url":f"{oauth_url}?{urllib.parse.urlencode(data)}"}


@frappe.whitelist(allow_guest = True)
def facebook_oauth_callback(code = None):
    code = frappe.form_dict.get("code")
    params = {
        "client_id" : '8216697681748093',
        "client_secret" : '73d7138847e324b21ffc251f8a3d29ae',
        "redirect_uri" : frappe.utils.get_url('/api/method/go1_meeting.go1_meeting.integration.validation.facebook_oauth_callback'),
        "code" : code
    }
    token_url = 'https://graph.facebook.com/v14.0/oauth/access_token'
    response = requests.post(token_url, data = params)
    frappe.log_error("facebook res sts",response.status_code)
    frappe.log_error("facebook access token",response.json())

@frappe.whitelist()
def authorize_facebook():
    oauth_url = "https://www.facebook.com/v14.0/dialog/oauth"
    params = {
        "scope" : "public_profile,email,pages_show_list,pages_manage_posts,pages_read_engagement,pages_manage_metadata",
        "redirect_uri" : frappe.utils.get_url("/api/method/go1_meeting.go1_meeting.integration.validation.facebook_oauth_callback"),
        'client_id': "8216697681748093",
        "client_secret":"73d7138847e324b21ffc251f8a3d29ae"
    }

    auth_url = f"{oauth_url}?{urllib.parse.urlencode(params)}"
    return auth_url

@frappe.whitelist(allow_guest = True)
def oauth_linkedin(code = None):
    code = frappe.form_dict.get("code")
    # encoded_state = frappe.form_dict("state")
    # state = urllib.parse.parse_qs(encoded_state)
    frappe.log_error("linkedin code",code)
    # frappe.log_error("linkedin state",state)
    # if code:
    frappe.log_error("code",code)
    exchange_url = "https://www.linkedin.com/oauth/v2/accessToken"
    headers = {"Content-Type":"application/x-www-form-urlencoded"}
    data={
        "grant_type":"authorization_code",
        "code":code,
        "redirect_uri":frappe.utils.get_url("/api/method/go1_meeting.go1_meeting.integration.validation.oauth_linkedin"),
        "client_id":"77wij4ejnipg99",
        "client_secret":"MjNQLAWu2fAOmqsI"
    }
    response = requests.post(url = exchange_url,headers = headers,data = data)
    frappe.log_error("linkedin redirect response",response.json())
    if response.status_code == 200:
        return {"status":"success","message":"Authorized"}
        
@frappe.whitelist()
def authorize_linkedin():
    oauth_url = "https://www.linkedin.com/oauth/v2/authorization"
    params = {
        "response_type":'code',
        "client_id":"77wij4ejnipg99",
        "scope": "openid profile w_member_social email rw_organization",
        "redirect_uri":frappe.utils.get_url("/api/method/go1_meeting.go1_meeting.integration.validation.oauth_linkedin"),
    }
    frappe.log_error("link auth url",f"{oauth_url}?{urllib.parse.urlencode(params)}")
    return f"{oauth_url}?{urllib.parse.urlencode(params)}"