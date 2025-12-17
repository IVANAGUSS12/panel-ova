from django.contrib.auth.views import LoginView, LogoutView
from django.urls import reverse_lazy
from .forms import LoginForm

class CustomLoginView(LoginView):
    template_name = 'accounts/login.html'
    authentication_form = LoginForm

    def get_success_url(self):
        return reverse_lazy('core:dashboard')

class CustomLogoutView(LogoutView):
    next_page = reverse_lazy('accounts:login')
