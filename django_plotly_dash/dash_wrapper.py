from dash import Dash
from flask import Flask

from django.urls import reverse
from django.http import HttpResponse

import json

from plotly.utils import PlotlyJSONEncoder

from .app_name import app_name, main_view_label

uid_counter = 0

usable_apps = {}
nd_apps = {}

def add_usable_app(name, app):
    global usable_apps
    usable_apps[name] = app

def add_instance(id, instance):
    global nd_apps
    nd_apps[id] = instance

def get_app_by_name(name):
    '''
    Locate a registered dash app by name, and return a DelayedDash instance encapsulating the app.
    '''
    return usable_apps.get(name,None)

def get_app_instance_by_id(id):
    '''
    Locate an instance of a dash app by identifier, or return None if one does not exist
    '''
    return nd_apps.get(id,None)

def clear_app_instance(id):
    del nd_apps[id]

def get_or_form_app(id, name, **kwargs):
    '''
    Locate an instance of a dash app by identifier, loading or creating a new instance if needed
    '''
    app = get_app_instance_by_id(id)
    if app:
        return app
    dd = get_app_by_name(name)
    return dd.form_dash_instance()

class Holder:
    def __init__(self):
        self.items = []
    def append_css(self, stylesheet):
        self.items.append(stylesheet)
    def append_script(self, script):
        self.items.append(script)

class DelayedDash:
    def __init__(self, name=None, **kwargs):
        if name is None:
            global uid_counter
            uid_counter += 1
            self._uid = "djdash_%i" % uid_counter
        else:
            self._uid = name
        self.layout = None
        self._callback_sets = []

        self.css = Holder()
        self.scripts = Holder()

        add_usable_app(self._uid,
                       self)

        self._expanded_callbacks = False

    def form_dash_instance(self, replacements=None, specific_identifier=None):
        rd = NotDash(name_root=self._uid,
                     app_pathname="%s:%s" % (app_name, main_view_label),
                     expanded_callbacks = self._expanded_callbacks,
                     replacements = replacements,
                     specific_identifier = specific_identifier)
        rd.layout = self.layout

        for cb, func in self._callback_sets:
            rd.callback(**cb)(func)
        for s in self.css.items:
            rd.css.append_css(s)
        for s in self.scripts.items:
            rd.scripts.append_script(s)

        return rd

    def callback(self, output, inputs=[], state=[], events=[]):
        callback_set = {'output':output,
                        'inputs':inputs,
                        'state':state,
                        'events':events}
        def wrap_func(func,callback_set=callback_set,callback_sets=self._callback_sets):
            callback_sets.append((callback_set,func))
            return func
        return wrap_func

    def expanded_callback(self, output, inputs=[], state=[], events=[]):
        self._expanded_callbacks = True
        return self.callback(output, inputs, state, events)

class NotFlask:
    def __init__(self):
        self.config = {}
        self.endpoints = {}

    def after_request(self,*args,**kwargs):
        pass
    def errorhandler(self,*args,**kwargs):
        return args[0]
    def add_url_rule(self,*args,**kwargs):
        route = kwargs['endpoint']
        self.endpoints[route] = kwargs
    def before_first_request(self,*args,**kwargs):
        pass
    def run(self,*args,**kwargs):
        pass

class NotDash(Dash):
    def __init__(self, name_root, app_pathname=None, replacements = None, specific_identifier=None, expanded_callbacks=False, **kwargs):

        if specific_identifier is not None:
            self._uid = specific_identifier
        else:
            self._uid = name_root

        add_instance(self._uid, self)

        self._flask_app = Flask(self._uid)
        self._notflask = NotFlask()
        self._base_pathname = reverse(app_pathname,kwargs={'id':self._uid})

        kwargs['url_base_pathname'] = self._base_pathname
        kwargs['server'] = self._notflask

        super(NotDash, self).__init__(**kwargs)

        self._adjust_id = False
        self._dash_dispatch = not expanded_callbacks
        if replacements:
            self._replacements = replacements
        else:
            self._replacements = dict()
        self._use_dash_layout = len(self._replacements) < 1

    def use_dash_dispatch(self):
        return self._dash_dispatch

    def use_dash_layout(self):
        return self._use_dash_layout

    def augment_initial_layout(self, base_response):
        if self.use_dash_layout() and False:
            return HttpResponse(base_response.data,
                                content_type=base_response.mimetype)
        # Adjust the base layout response
        baseDataInBytes = base_response.data
        baseData = json.loads(baseDataInBytes.decode('utf-8'))
        # Walk tree. If at any point we have an element whose id matches, then replace any named values at this level
        reworked_data = self.walk_tree_and_replace(baseData)
        response_data = json.dumps(reworked_data,
                                   cls=PlotlyJSONEncoder)
        return HttpResponse(response_data,
                            content_type=base_response.mimetype)

    def walk_tree_and_replace(self, data):
        # Walk the tree. Rely on json decoding to insert instances of dict and list
        # ie we use a dna test for anatine, rather than our eyes and ears...
        if isinstance(data,dict):
            response = {}
            replacements = {}
            # look for id entry
            thisID = data.get('id',None)
            if thisID is not None:
                replacements = self._replacements.get(thisID,{})
            # walk all keys and replace if needed
            for k, v in data.items():
                r = replacements.get(k,None)
                if r is None:
                    r = self.walk_tree_and_replace(v)
                response[k] = r
            return response
        if isinstance(data,list):
            # process each entry in turn and return
            return [self.walk_tree_and_replace(x) for x in data]
        return data

    def flask_app(self):
        return self._flask_app

    def base_url(self):
        return self._base_pathname

    def app_context(self, *args, **kwargs):
        return self._flask_app.app_context(*args,
                                           **kwargs)

    def test_request_context(self, *args, **kwargs):
        return self._flask_app.test_request_context(*args,
                                                    **kwargs)

    def locate_endpoint_function(self, name=None):
        if name is not None:
            ep = "%s_%s" %(self._base_pathname,
                           name)
        else:
            ep = self._base_pathname
        return self._notflask.endpoints[ep]['view_func']

    @Dash.layout.setter
    def layout(self, value):

        if self._adjust_id:
            self._fix_component_id(value)
        return Dash.layout.fset(self, value)

    def _fix_component_id(self, component):

        theID = getattr(component,"id",None)
        if theID is not None:
            setattr(component,"id",self._fix_id(theID))
        try:
            for c in component.children:
                self._fix_component_id(c)
        except:
            pass

    def _fix_id(self, name):
        if not self._adjust_id:
            return name
        return "%s_-_%s" %(self._uid,
                           name)

    def _fix_callback_item(self, item):
        item.component_id = self._fix_id(item.component_id)
        return item

    def callback(self, output, inputs=[], state=[], events=[]):
        return super(NotDash, self).callback(self._fix_callback_item(output),
                                             [self._fix_callback_item(x) for x in inputs],
                                             [self._fix_callback_item(x) for x in state],
                                             [self._fix_callback_item(x) for x in events])

    def dispatch(self):
        import flask
        body = flask.request.get_json()
        return self. dispatch_with_args(body, argMap=dict())

    def dispatch_with_args(self, body, argMap):
        inputs = body.get('inputs', [])
        state = body.get('state', [])
        output = body['output']

        target_id = '{}.{}'.format(output['id'], output['property'])
        args = []
        for component_registration in self.callback_map[target_id]['inputs']:
            args.append([
                c.get('value', None) for c in inputs if
                c['property'] == component_registration['property'] and
                c['id'] == component_registration['id']
            ][0])

        for component_registration in self.callback_map[target_id]['state']:
            args.append([
                c.get('value', None) for c in state if
                c['property'] == component_registration['property'] and
                c['id'] == component_registration['id']
            ][0])

        return self.callback_map[target_id]['callback'](*args,**argMap)


