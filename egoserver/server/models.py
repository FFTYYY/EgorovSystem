from django.db import models

# Create your models here.

class Variable(models.Model):
    key = models.TextField(default = "", blank = True, null = True)
    val = models.TextField(default = "", blank = True, null = True)

    def __str__(self):
        return str(self.key)
    
class Function(models.Model):
    key = models.TextField(default = "", blank = True, null = True)
    val = models.TextField(default = "", blank = True, null = True)

    def __str__(self):
        return str(self.key)

class Password(models.Model):
    key = models.TextField(default = "", blank = True, null = True)
    val = models.TextField(default = "", blank = True, null = True)

    def __str__(self):
        return str(self.key)

