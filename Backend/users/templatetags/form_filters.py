from django import template
from django.utils.safestring import mark_safe

register = template.Library()

@register.filter(name='attr')
def set_attr(field, attr_string):
    """
    Set HTML attributes on a form field
    Usage: {{ form.field|attr:"class:form-control,placeholder:Enter text" }}
    """
    attrs = {}
    for attr_pair in attr_string.split(','):
        key, value = attr_pair.split(':')
        attrs[key.strip()] = value.strip()
    
    return field.as_widget(attrs=attrs)

@register.filter(name='add_class')
def add_class(field, css_class):
    return field.as_widget(attrs={'class': css_class})

@register.filter(name='add_placeholder')
def add_placeholder(field, placeholder_text):
    return field.as_widget(attrs={'placeholder': placeholder_text})