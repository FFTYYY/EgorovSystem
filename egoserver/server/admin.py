from django.contrib import admin
from .models import Variable , Function, Password

class VariableAdmin(admin.ModelAdmin):
    pass
class FunctionAdmin(admin.ModelAdmin):
    pass
class PasswordAdmin(admin.ModelAdmin):
    pass

# Register your models here.
admin.site.register(Variable     , VariableAdmin  )
admin.site.register(Function     , FunctionAdmin  ) 
admin.site.register(Password     , PasswordAdmin  ) 
