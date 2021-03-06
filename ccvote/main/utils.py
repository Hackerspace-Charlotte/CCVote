import datetime
from django import forms
from django.core.exceptions import ObjectDoesNotExist
from django.template import Context
from django.template import RequestContext
from django.template import loader
from django.template import TemplateDoesNotExist
from django.utils.html import escape
from django.utils.html import conditional_escape
from django.utils.log import getLogger
from django.http import QueryDict
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from main.authorization import auth_template_vars
from models import *
from django.db.models.fields import DateField
from django.db.models.fields.related import ForeignKey

logger = getLogger('debugging')

simple_response = '''<html>
<head><title>{response_string}</title></head>
<body><h1>{response_string}</h1><p>{details}</p></body>
</html>'''

def instance_dict(instance, key_format=None):
    """
    Returns a dictionary containing field names and values for the given
    instance
    http://djangosnippets.org/snippets/199/
    """
    if key_format:
        assert '%s' in key_format, 'key_format must contain a %s'
    key = lambda key: key_format and key_format % key or key

    pk = instance._get_pk_val()
    d = {}
    for field in instance._meta.fields:
        attr = field.name
        if hasattr(instance, attr): # django filer broke without this check
            value = getattr(instance, attr)
            if value is not None:
                if isinstance(field, ForeignKey):
                    value = value._get_pk_val()
                elif isinstance(field, DateField):
                    value = value.isoformat()
        else:
            value = None
        d[key(attr)] = value
    return d

class BoolSelect(forms.Select):
    '''Fixed drop-down select for BOOLEAN_CHOICES'''

    def render_option(self, selected_choices, option_value, option_label):
        # Output is very much not expected.
        # Why in the world "selected_choices" 
        # based on the *labels*?
        # logger.debug('What is selected: %s %s %s' % (
        #         repr(selected_choices),
        #         repr(option_value),
        #         repr(option_label)))
        option_value = option_value
        if (unicode(option_label) in selected_choices):
            selected_html = ' selected="selected"'
        else:
            selected_html = ''
        return '<option value="%s"%s>%s</option>' % (
            escape(option_value), selected_html,
            conditional_escape(option_label))

class ActionView:
    '''
    This is a base class convenience request handler.  The idea is that,
    regardless of the method of data transfer, the programmer will only
    have to define an action_<name>() method to process that data.  The
    class will take care of normalizing data sent via a GET query 
    string, a POST uri-encoded body, or even "cruft" free URL params.

    The only stipulation is that you implement an action_<name>(args)
    method where args is a django QueryDict object and a variable
    "action_<name>" exists in the QueryDict.  Which is easy because
    there is usually a type="submit" input that will be named 
    "action_<name>".

    In addition to automatic action dispatch there will be a set of 
    convenience functions and member variables.  self.data will serve
    as a cache of transaction results to render into the table
    automatically.
    '''

    def __init__(self):
        # Request/response objects as built by django
        self.request = None
        # response headers
        self.response_headers = {}
        # template data
        self.data = {}
        # list of defined actions for the request handler
        self._actions = []
        # set up the action via introspection
        self._init_actions()

    def _init_actions(self):
        prefix = 'action_'
        prefix_len = len(prefix)
        for attr in dir(self):
            if callable(getattr(self, attr)) and (attr[:prefix_len] == prefix):
                self._actions.append(attr)

    def _dispatch(self, args):
        for action in self._actions:
            if action in args:
                method = getattr(self, action)
                return method(args)
        return self.action(args)

    def not_implemented(self):
        data = dict(response_string='501 Not implemented', details='')
        try:
            template = loader.get_template('main/error_page.html')
            c = Context(data)
            body = template.render(c)
        except TemplateDoesNotExist:
            body = str.format(simple_response, **data)
        return HttpResponse(content=body,
                            content_type='text/html; charset=utf-8',
                            status=501)

    def not_found(self, details=''):
        '''This can also be overridden if a more detailed 404 is required'''
        if not details:
            details = self.request.path
        data = dict(response_string='404 Not found', details=details)
        try:
            template = loader.get_template('main/not_found.html')
            c = Context(data)
            body = template.render(c)
        except TemplateDoesNotExist:
            body = str.format(simple_response, **data)
        return HttpResponse(content=body,
                            content_type='text/html; charset=utf-8',
                            status=404)

    def render_html(self, template):
        template = loader.get_template(template)
        c = RequestContext(self.request, self.data)
        return HttpResponse(content=template.render(c),
                            content_type='text/html; charset=utf-8',
                            status=200)

    def redirect(self, location):
        return HttpResponseRedirect(location)

    @classmethod
    def as_view(cls):
        def aux(request, *args, **kwargs):
            handler = cls()
            return handler(request, *args, **kwargs)
        return aux

    def default(self):
        '''Method place-holder.  Override.'''
        return self.not_implemented()

    def action(self, args):
        '''Method place-holder.  Override.'''
        return self.not_implemented()
    
    def __call__(self, request, *args, **kwargs):
        self.url_args = args
        self.request = request
        #
        # Normalize request data into an "args" parameter.
        # "args" will be a QueryDict in _all_ cases.
        # URL arguments always override other data (for now)
        #
        self.data['request'] = request
        self.data['user_name'] = Security.get_user_name(request)
        auth_template_vars(request, self.data)
        if request.method == 'GET':
            if request.GET:
                args = request.GET.copy()
                args.update(kwargs) # we'll see how this works
                return self._dispatch(args)
            elif kwargs:
                args = QueryDict('').copy()
                args.update(kwargs)
                # Fake out url regex args as form args
                # that will then allow binding and 
                # futher validation
                args.update(kwargs)
                return self._dispatch(args)
            else:
                return self.default()
        elif request.method == 'POST':
            if request.POST:
                args = request.POST.copy()
                args.update(kwargs) # we'll see how this works
                return self._dispatch(args)
        else:
            return self.not_implemented()

class Security:

    @classmethod
    def validate_user(cls, user_name, password):
        user = Security.get_user(user_name)
        if user is None:
            return False
        return user.verify_password(password)

    @classmethod
    def get_user(cls, user_name):
        try:
            return UserData.objects.get(user_name=user_name)
        except:
            return None

    @classmethod
    def get_current_user(cls, request):
        return Security.get_user(request, Security.get_user_name(request))

    @classmethod
    def get_user_name(cls, request):
        return request.session.get('user_name')

    @classmethod
    def get_user_id(cls, request):
        return request.session.get('user_id')

    @classmethod
    def set_user_name(cls, request, user_name):
        request.session['user_name'] = user_name

    @classmethod
    def set_user_id(cls, request, user_id):
        request.session['user_id'] = user_id

    @classmethod
    def login(cls, request, user_name, password):
        '''
        Perform a login using a utf8 encoded user_name/password combo
        Returns False on authentication error
        '''
        try:
            user = UserData.objects.get(user_name=user_name)
        except ObjectDoesNotExist:
            return False

        if not user.verify_password(password):
            return False

        # TODO: log actions in LogData
        user.user_last_login = datetime.datetime.now()
        user.user_status = 'logged_in'
        user.save()

        # Authorization is driven off of user_id
        Security.set_user_id(request, user.user_id)
        # Username is handy to have in the session in any case
        Security.set_user_name(request, user.user_name)
        return True

    @classmethod
    def logout(cls, request):
        user = UserData.objects.get(user_name=Security.get_user_name(request))
        user.user_status = 'logged_out'
        user.save()
        Security.set_user_id(request, None)
        Security.set_user_name(request, None)

    @classmethod
    def loggedin(cls, request):
        return Security.get_user_name(request) is not None

class MeetingState:

    @classmethod
    def set_user_vote(cls, motion_id, user_id, sent_vote):
        # Set the current user's vote

        motion = MotionData.objects.get(motion_id = motion_id)
        user = UserData.objects.get(user_id = user_id)

        try:
            vote = VoteData.objects.get(motion_id=motion, user_id=user)
        except VoteData.DoesNotExist:
            vote = VoteData(motion_id=motion, user_id=user)

        vote.vote_time = datetime.datetime.utcnow()
        vote.vote = sent_vote
        vote.save()

        return vote

    @classmethod
    def get_current_motion(cls):
        # Nothing passed, as all we're doing here is returning the
        # current motion (well, "latest" motion based on the
        # 'motion_vote_start' date/time).
        #
        # Question -- should this be renamed to 'open_motion', with
        # a purposefully ambiguous name relating to its ambiguous
        # function?  Ie, call 'meeting_state.open_motion' to return
        # the current open motion, and if there's not one currently
        # open (motion_vote_start exists, but motion_vote_end is null), 
        # then it should create one (and return it)?
        # (using django method 'get_or_create' -- so maybe name it
        # 'get_or_create_current_motion')
        return MotionData.objects.select_related().latest('motion_vote_start')

    @classmethod
    def get_motion_by_id(cls, sent_motion_id):
        try:
            local_motion = MotionData.objects.select_related().get(
                motion_id=sent_motion_id)
        except ObjectDoesNotExist:
            return False
        return local_motion

    @classmethod
    def get_votes_in_motion(cls, sent_motion):
        # returns a list of 'VoteData' objects (and related
        # objects) associated with the passed 'MotionData'
        # object.
        #
        # If you call this and try to iterate through the
        # returned 'VoteData's, you'll only see votes for
        # people that've actually cast a vote.  If you want
        # to see everybody that's logged in, use
        # 'MeetingState.get_logged_in_users()'.
        try:
            local_votes = VoteData.objects.select_related().filter(
                motion_id=sent_motion.motion_id)
        except ObjectDoesNotExist:
            return False
        return local_votes

    @classmethod
    def get_votes_by_motion_id(cls, sent_motion_id):
        # returns a list of 'VoteData' objects (and related
        # objects) associated with the passed id of a matching
        # 'MotionData' object.
        #
        # If you call this and try to iterate through the
        # returned 'VoteData's, you'll only see votes for
        # people that've actually cast a vote.  If you want
        # to see everybody that's logged in, use
        # 'MeetingState.get_logged_in_users()'.
        try:
            local_votes = VoteData.objects.select_related().filter(
                motion_id=sent_motion_id)
        except ObjectDoesNotExist:
            return False
        return local_votes

    @classmethod
    def get_logged_in_users(cls):
        # returns a list of 'UserData' objects
        try:
            local_users = UserData.objects.filter(user_status='logged_in')
        except ObjectDoesNotExist:
            return False
        return local_users

    @classmethod
    def get_users_can_vote(cls):
        # returns a list of 'UserData' objects
        try:
            local_users = UserData.objects.filter(models.Q(group_id=1)|models.Q(group_id=2))
        except ObjectDoesNotExist:
            return False
        return local_users

    @classmethod
    def get_current_meeting_motions(cls):
        # returns a list of 'MotionData's with the same
        # meeting_id as...  oh wait, we don't have that yet.
        #
        # Okay -- I'm just going to return the last 5 motions...
        #
        # Sorting ascending by 'motion_vote_start', then reversing
        # and returning only the first 5.  That'll put them in
        # 'most-recent-first' order.
        return MotionData.objects.select_related().order_by(
            'motion_vote_start').reverse()[:5]

    @classmethod
    def meeting_has_changed(cls, date_time):
        # Return True if the meeting has changed since
        #     the passed time
        # Return False if nothing's changed
        latest_vote = VoteData.objects.order_by('vote_time').reverse()[0]
        if latest_vote.vote_time > date_time:
            return True
        else:
            return False


# vim: set sts=4 sw=4 expandtab:
